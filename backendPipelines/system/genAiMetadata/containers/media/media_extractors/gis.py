# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""GIS container formats with an open reader in the standard library: a GeoPackage (`.gpkg`, an SQLite database
whose `gpkg_contents` / `gpkg_geometry_columns` / `gpkg_spatial_ref_sys` tables describe every layer) and an
ArcGIS Pro layer file (`.lyrx`, a JSON document in the CIM schema that names a layer and where its data lives).

Both write the `sys_geo` group the GeoJSON extractor writes, so the metadata catalog promotes `footprint` to the
`location` row the same way: `format`, `featureCount`, `geometryTypes`, `footprint`, plus `layers[]` (one entry
per layer) and `crs`. A footprint is written in WGS84 only; a layer in another spatial reference keeps its
`nativeExtent` with its `crs` and the footprint stays `None`, because this module carries no reprojection.

A GeoPackage is read through SQLite, which pages the file on demand: the extractor reads the three metadata
tables, each layer's `PRAGMA table_info`, and a `COUNT(*)` bounded by `DATA_MAX_ROWS_COUNTED`. It never reads a
geometry blob or a feature row, so the cost does not grow with the file. A layer file is a small document and is
parsed whole, bounded by `TEXT_HEAD_BYTES`.
"""

import json
import os
import sqlite3
from typing import Dict, List, Optional, Tuple

from .common import (
    BranchResult,
    CLASS_DATA,
    DATA_MAX_COLUMNS,
    DATA_MAX_ROWS_COUNTED,
    ExtractContext,
    TEXT_HEAD_BYTES,
    human_count,
    other_fallback,
    truncate_text,
)

# Layers recorded per file; a GeoPackage carrying more lists the first ones and says so.
GIS_MAX_LAYERS = 50
# Spatial reference ids whose coordinates are WGS84 longitude/latitude, the only ones a footprint is written for.
WGS84_SRS_IDS = frozenset({4326})
_LYRX_FORMAT = "ArcGIS Layer File"
_GPKG_FORMAT = "GeoPackage"
# SQLite's magic string; a `.gpkg` whose first bytes differ is not a database.
_SQLITE_MAGIC = b"SQLite format 3\x00"
# The GeoPackage geometry type words that name a GeoJSON geometry type (ISO 13249 / GeoPackage §2.1.3).
_GPKG_GEOMETRY_TYPES = {
    "POINT": "Point", "LINESTRING": "LineString", "POLYGON": "Polygon", "MULTIPOINT": "MultiPoint",
    "MULTILINESTRING": "MultiLineString", "MULTIPOLYGON": "MultiPolygon", "GEOMETRYCOLLECTION": "GeometryCollection",
    "GEOMETRY": "Geometry",
}
# CIM layer class names (the `type` of a layer definition) and the plain words recorded for them.
_CIM_LAYER_KINDS = {
    "CIMFeatureLayer": "feature", "CIMRasterLayer": "raster", "CIMGroupLayer": "group",
    "CIMAnnotationLayer": "annotation", "CIMTinLayer": "tin", "CIMLasDatasetLayer": "las",
    "CIMMosaicLayer": "mosaic", "CIMVectorTileLayer": "vector-tile", "CIMTiledServiceLayer": "tiled-service",
    "CIMBuildingLayer": "building", "CIMSceneServiceLayer": "scene-service", "CIMStandaloneTable": "table",
}


def extract_gis(path: str, ctx: ExtractContext) -> BranchResult:
    extension = (ctx.extension or "").lower()
    if extension == ".gpkg":
        return extract_geopackage(path, ctx)
    if extension == ".lyrx":
        return extract_lyrx(path, ctx)
    return other_fallback(ctx, f"{extension or 'the file'} is not a GIS format this extractor reads")


# --- GeoPackage ---------------------------------------------------------------------------------------------------


def _is_sqlite(path: str) -> bool:
    with open(path, "rb") as handle:
        return handle.read(len(_SQLITE_MAGIC)) == _SQLITE_MAGIC


def _table_names(connection: sqlite3.Connection) -> frozenset:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')").fetchall()
    return frozenset(str(row[0]) for row in rows)


def _srs_names(connection: sqlite3.Connection, tables: frozenset) -> Dict[int, dict]:
    if "gpkg_spatial_ref_sys" not in tables:
        return {}
    out: Dict[int, dict] = {}
    for srs_id, name, organization, code in connection.execute(
            "SELECT srs_id, srs_name, organization, organization_coordsys_id FROM gpkg_spatial_ref_sys"):
        try:
            out[int(srs_id)] = {"name": name, "organization": organization, "code": code}
        except (TypeError, ValueError):
            continue
    return out


def _crs_label(srs_id: Optional[int], srs: Dict[int, dict]) -> Optional[str]:
    """`EPSG:4326` for a registered authority code; the raw id when the registry does not name it. The GeoPackage
    spec reserves -1 (undefined Cartesian) and 0 (undefined geographic), registered under organization `NONE`;
    neither names a spatial reference."""
    if srs_id is None or srs_id in (-1, 0):
        return None
    entry = srs.get(srs_id)
    if entry and str(entry.get("organization") or "").upper() == "NONE":
        return None
    if entry and entry.get("organization") and entry.get("code") is not None:
        return f"{str(entry['organization']).upper()}:{entry['code']}"
    return f"SRID:{srs_id}"


def _bounded_count(connection: sqlite3.Connection, table: str) -> Tuple[int, bool]:
    """Rows in `table`, counted up to DATA_MAX_ROWS_COUNTED + 1 so a cap is reported rather than guessed."""
    quoted = '"' + table.replace('"', '""') + '"'
    row = connection.execute(
        f"SELECT COUNT(*) FROM (SELECT 1 FROM {quoted} LIMIT ?)", (DATA_MAX_ROWS_COUNTED + 1,)).fetchone()
    count = int(row[0]) if row else 0
    if count > DATA_MAX_ROWS_COUNTED:
        return DATA_MAX_ROWS_COUNTED, True
    return count, False


def _columns(connection: sqlite3.Connection, table: str, geometry_column: Optional[str]) -> List[str]:
    quoted = '"' + table.replace('"', '""') + '"'
    names = [str(row[1]) for row in connection.execute(f"PRAGMA table_info({quoted})")]
    return [name for name in names if name != geometry_column][:DATA_MAX_COLUMNS]


def _extent(row) -> Optional[List[float]]:
    try:
        values = [float(value) for value in row]
    except (TypeError, ValueError):
        return None
    return values if all(value == value for value in values) else None  # no NaN


def _union_extent(extents: List[List[float]]) -> Optional[List[float]]:
    if not extents:
        return None
    return [min(e[0] for e in extents), min(e[1] for e in extents), max(e[2] for e in extents), max(e[3] for e in extents)]


def _footprint_from_extent(extent: List[float]) -> dict:
    min_x, min_y, max_x, max_y = extent
    if min_x == max_x and min_y == max_y:
        return {"type": "Point", "coordinates": [min_x, min_y]}
    ring = [[min_x, min_y], [max_x, min_y], [max_x, max_y], [min_x, max_y], [min_x, min_y]]
    return {"type": "Polygon", "coordinates": [ring]}


def extract_geopackage(path: str, ctx: ExtractContext) -> BranchResult:
    if not _is_sqlite(path):
        return other_fallback(ctx, "GeoPackage is not an SQLite database")
    result = BranchResult(file_class=CLASS_DATA)
    uri = "file:" + os.path.abspath(path).replace("?", "%3f").replace("#", "%23") + "?mode=ro&immutable=1"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        return other_fallback(ctx, f"GeoPackage could not be opened: {exc}")
    layers: List[dict] = []
    layer_total = 0
    try:
        tables = _table_names(connection)
        if "gpkg_contents" not in tables:
            return other_fallback(ctx, "SQLite database carries no gpkg_contents table; not a GeoPackage")
        srs = _srs_names(connection, tables)
        geometry_columns: Dict[str, Tuple[str, str, int]] = {}
        if "gpkg_geometry_columns" in tables:
            for table_name, column_name, geometry_type, srs_id in connection.execute(
                    "SELECT table_name, column_name, geometry_type_name, srs_id FROM gpkg_geometry_columns"):
                geometry_columns[str(table_name)] = (str(column_name), str(geometry_type or "").upper(), srs_id)
        rows = connection.execute(
            "SELECT table_name, data_type, identifier, description, min_x, min_y, max_x, max_y, srs_id "
            "FROM gpkg_contents ORDER BY table_name").fetchall()
        layer_total = len(rows)
        for table_name, data_type, identifier, description, min_x, min_y, max_x, max_y, srs_id in rows[:GIS_MAX_LAYERS]:
            table_name = str(table_name)
            geometry = geometry_columns.get(table_name)
            layer: Dict[str, object] = {
                "name": identifier or table_name,
                "table": table_name,
                "type": str(data_type or "unknown"),
            }
            if description:
                layer["description"] = truncate_text(str(description), 500)
            try:
                layer_srs = int(srs_id) if srs_id is not None else None
            except (TypeError, ValueError):
                layer_srs = None
            if geometry is not None:
                layer["geometryType"] = _GPKG_GEOMETRY_TYPES.get(geometry[1], geometry[1].title() or "Geometry")
                if layer_srs is None and geometry[2] is not None:
                    try:
                        layer_srs = int(geometry[2])
                    except (TypeError, ValueError):
                        layer_srs = None
            crs = _crs_label(layer_srs, srs)
            if crs:
                layer["crs"] = crs
            layer["srsId"] = layer_srs
            extent = _extent((min_x, min_y, max_x, max_y))
            if extent is not None:
                layer["nativeExtent"] = extent
            if table_name in tables and str(data_type) in ("features", "attributes"):
                try:
                    count, capped = _bounded_count(connection, table_name)
                    layer["featureCount"] = count
                    layer["featureCountExact"] = not capped
                    layer["fields"] = _columns(connection, table_name, geometry[0] if geometry else None)
                except sqlite3.Error as exc:
                    result.warnings.append(f"GeoPackage layer {table_name!r} could not be read ({exc})")
            layers.append(layer)
    except sqlite3.Error as exc:
        return other_fallback(ctx, f"GeoPackage metadata could not be read: {exc}")
    finally:
        connection.close()
    if layer_total > GIS_MAX_LAYERS:
        result.warnings.append(f"GeoPackage lists {layer_total} layers; the first {GIS_MAX_LAYERS} are recorded")
    feature_layers = [layer for layer in layers if layer.get("type") == "features"]
    geometry_types = sorted({str(layer["geometryType"]) for layer in feature_layers if "geometryType" in layer})
    crs_values = sorted({str(layer["crs"]) for layer in layers if layer.get("crs")})
    feature_count = sum(int(layer.get("featureCount", 0)) for layer in feature_layers)
    count_exact = all(layer.get("featureCountExact", True) for layer in feature_layers)
    # The footprint is the union of the WGS84 layers' extents; other spatial references keep their own extent.
    wgs84_extents = [layer["nativeExtent"] for layer in layers
                     if layer.get("srsId") in WGS84_SRS_IDS and "nativeExtent" in layer]
    footprint = None
    union = _union_extent(wgs84_extents)
    if union is not None and -180.0 <= union[0] <= union[2] <= 180.0 and -90.0 <= union[1] <= union[3] <= 90.0:
        footprint = _footprint_from_extent(union)
    elif layers and not wgs84_extents and any("nativeExtent" in layer for layer in layers):
        result.warnings.append(
            "GeoPackage layers are not in EPSG:4326; the native extents are recorded and no footprint is written")
    sys_geo: Dict[str, object] = {
        "format": _GPKG_FORMAT,
        "type": "GeoPackage",
        "layerCount": layer_total,
        "layers": layers,
        "featureCount": feature_count,
        "featureCountExact": count_exact,
        "geometryTypes": geometry_types,
        "crs": crs_values[0] if len(crs_values) == 1 else crs_values,
        "footprint": footprint,
    }
    result.attributes["sys_geo"] = sys_geo
    result.facts["layers"] = human_count(layer_total, "layer")
    if feature_layers:
        result.facts["features"] = human_count(feature_count, "feature") + ("" if count_exact else " or more")
    if geometry_types:
        result.facts["geometryTypes"] = ", ".join(geometry_types)
    if crs_values:
        result.facts["crs"] = ", ".join(crs_values)
    if footprint is not None:
        result.facts["footprint"] = str(footprint["type"]) + " bounding box"
    summary = {
        "format": _GPKG_FORMAT,
        "layers": [{key: layer[key] for key in ("name", "type", "geometryType", "featureCount", "crs", "fields")
                    if key in layer} for layer in layers],
    }
    result.text_excerpt = truncate_text(json.dumps(summary, default=str), ctx.max_text_chars)
    return result


# --- ArcGIS Pro layer file (.lyrx) ------------------------------------------------------------------------------


def _walk_layer_definitions(document: dict) -> List[dict]:
    definitions = document.get("layerDefinitions")
    return [entry for entry in definitions if isinstance(entry, dict)] if isinstance(definitions, list) else []


# Keys of a `workspaceConnectionString` pair (an `;`-separated `KEY=value` list) that carry a credential.
_CONNECTION_SECRET_KEYS = ("PASSWORD", "ENCRYPTED_PASSWORD", "TOKEN", "SECRET", "AUTHENTICATION_MODE")


def _scrub_connection_string(value: str) -> str:
    """The connection string without its credential pairs: the dataset's workspace stays identifiable (DATABASE,
    SERVER, INSTANCE, VERSION) and nothing a client could authenticate with is recorded."""
    kept = []
    for pair in value.split(";"):
        key = pair.split("=", 1)[0].strip().upper()
        if any(secret in key for secret in _CONNECTION_SECRET_KEYS):
            continue
        kept.append(pair)
    return ";".join(kept)


def _data_connection(definition: dict) -> Dict[str, object]:
    """The dataset a layer points at, as the document records it: a dataset name, the workspace path or URL, and
    the connection class, with the credentials a service connection may carry left out."""
    table = definition.get("featureTable") if isinstance(definition.get("featureTable"), dict) else definition
    connection = table.get("dataConnection") if isinstance(table, dict) else None
    if not isinstance(connection, dict):
        return {}
    out: Dict[str, object] = {}
    for source_key, target_key in (("type", "type"), ("dataset", "dataset"), ("datasetType", "datasetType"),
                                   ("workspaceFactory", "workspaceFactory"), ("featureDataset", "featureDataset"),
                                   ("url", "url"), ("workspaceConnectionString", "workspace")):
        value = connection.get(source_key)
        if isinstance(value, str) and value:
            if source_key == "workspaceConnectionString":
                value = _scrub_connection_string(value)
            out[target_key] = truncate_text(value, 500)
    return out


def _display_fields(definition: dict) -> List[str]:
    table = definition.get("featureTable") if isinstance(definition.get("featureTable"), dict) else None
    fields = table.get("fieldDescriptions") if isinstance(table, dict) else None
    names: List[str] = []
    for entry in fields if isinstance(fields, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("fieldName"), str):
            names.append(entry["fieldName"])
    return names[:DATA_MAX_COLUMNS]


def _renderer_type(definition: dict) -> Optional[str]:
    renderer = definition.get("renderer")
    if isinstance(renderer, dict) and isinstance(renderer.get("type"), str):
        return renderer["type"]
    return None


def _extent_from_cim(definition: dict) -> Tuple[Optional[List[float]], Optional[str]]:
    """A CIM layer rarely stores its extent; when it does, as `{xmin, ymin, xmax, ymax, spatialReference}`, the
    extent and the spatial reference's `wkid` are returned."""
    for key in ("extent", "customFullExtent"):
        extent = definition.get(key)
        if isinstance(extent, dict) and all(k in extent for k in ("xmin", "ymin", "xmax", "ymax")):
            values = _extent((extent["xmin"], extent["ymin"], extent["xmax"], extent["ymax"]))
            reference = extent.get("spatialReference")
            wkid = reference.get("latestWkid") or reference.get("wkid") if isinstance(reference, dict) else None
            return values, (f"EPSG:{wkid}" if isinstance(wkid, int) else None)
    return None, None


def extract_lyrx(path: str, ctx: ExtractContext) -> BranchResult:
    size = os.path.getsize(path)
    if size > TEXT_HEAD_BYTES:
        return other_fallback(ctx, f"layer file is {size} bytes, above the {TEXT_HEAD_BYTES}-byte bound for a document")
    try:
        with open(path, "rb") as handle:
            document = json.loads(handle.read().decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        return other_fallback(ctx, f"layer file is not a JSON document ({exc})")
    if not isinstance(document, dict) or document.get("type") != "CIMLayerDocument":
        return other_fallback(ctx, "JSON document is not a CIMLayerDocument")
    result = BranchResult(file_class=CLASS_DATA)
    layers: List[dict] = []
    footprint = None
    crs_values: List[str] = []
    for definition in _walk_layer_definitions(document)[:GIS_MAX_LAYERS]:
        cim_type = str(definition.get("type") or "")
        layer: Dict[str, object] = {
            "name": definition.get("name") or definition.get("uRI") or cim_type,
            "type": _CIM_LAYER_KINDS.get(cim_type, cim_type or "unknown"),
            "cimType": cim_type,
        }
        if isinstance(definition.get("visibility"), bool):
            layer["visible"] = definition["visibility"]
        source = _data_connection(definition)
        if source:
            layer["source"] = source
        fields = _display_fields(definition)
        if fields:
            layer["fields"] = fields
        renderer = _renderer_type(definition)
        if renderer:
            layer["renderer"] = renderer
        if isinstance(definition.get("layers"), list):
            layer["memberCount"] = len(definition["layers"])
        extent, crs = _extent_from_cim(definition)
        if extent is not None:
            layer["nativeExtent"] = extent
        if crs:
            layer["crs"] = crs
            crs_values.append(crs)
            if crs == "EPSG:4326" and extent is not None and footprint is None:
                footprint = _footprint_from_extent(extent)
        layers.append(layer)
    definition_total = len(_walk_layer_definitions(document))
    if definition_total > GIS_MAX_LAYERS:
        result.warnings.append(f"layer file lists {definition_total} layers; the first {GIS_MAX_LAYERS} are recorded")
    if not layers:
        result.warnings.append("layer file carries no layer definitions")
    kinds = sorted({str(layer["type"]) for layer in layers})
    sources = sorted({str(layer["source"]["dataset"]) for layer in layers
                      if isinstance(layer.get("source"), dict) and layer["source"].get("dataset")})
    unique_crs = sorted(set(crs_values))
    sys_geo: Dict[str, object] = {
        "format": _LYRX_FORMAT,
        "type": "CIMLayerDocument",
        "version": document.get("version"),
        "layerCount": definition_total,
        "layers": layers,
        "layerKinds": kinds,
        "datasets": sources[:GIS_MAX_LAYERS],
        "featureCount": 0,
        "geometryTypes": [],
        "crs": unique_crs[0] if len(unique_crs) == 1 else unique_crs,
        "footprint": footprint,
    }
    result.attributes["sys_geo"] = sys_geo
    result.facts["layers"] = human_count(definition_total, "layer")
    if kinds:
        result.facts["layerKinds"] = ", ".join(kinds)
    if sources:
        result.facts["datasets"] = ", ".join(sources[:5])
    if footprint is not None:
        result.facts["footprint"] = str(footprint["type"]) + " bounding box"
    result.warnings.append(
        "a layer file references its data by name; the dataset itself is not in this file")
    summary = {"format": _LYRX_FORMAT, "layers": [
        {key: layer[key] for key in ("name", "type", "source", "fields", "renderer") if key in layer} for layer in layers]}
    result.text_excerpt = truncate_text(json.dumps(summary, default=str), ctx.max_text_chars)
    return result
