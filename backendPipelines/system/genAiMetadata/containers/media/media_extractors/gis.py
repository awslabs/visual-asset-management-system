# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""GIS container formats with an open reader in the standard library: a GeoPackage (`.gpkg`, an SQLite database
whose `gpkg_contents` / `gpkg_geometry_columns` / `gpkg_spatial_ref_sys` tables describe every layer) and an
ArcGIS Pro layer file (`.lyrx`, a JSON document in the CIM schema that names a layer and where its data lives).

Both write the `sys_geo` group the GeoJSON extractor writes, so the metadata catalog promotes `footprint` to the
`location` row the same way: `format`, `footprint`, `layers[]` (one entry per layer) and `crs`; a GeoPackage also
records `featureCount` and `geometryTypes`, which a layer file cannot know. A footprint is written in WGS84 only; a
layer in another spatial reference keeps its `nativeExtent` with its `crs` and the footprint stays `None`, because
this module carries no reprojection.

A GeoPackage is read through SQLite, which pages the file on demand. The extractor counts the `gpkg_contents` rows,
reads the first GIS_MAX_LAYERS of them and only the geometry-column and spatial-reference rows those layers name,
then each listed table's `PRAGMA table_info` and a `COUNT(*)` bounded by `DATA_MAX_ROWS_COUNTED`. It never reads a
geometry blob or decodes a feature row, and an SQLite progress handler interrupts any statement that outlives the
file's time budget (GPKG_SQL_BUDGET_SECONDS, of which one layer's count may take GPKG_LAYER_BUDGET_SECONDS). A view
listed as a layer is not counted. A layer file is a small document and is parsed whole, bounded by
`TEXT_HEAD_BYTES`. Every name taken from either file is cut to DATA_CELL_MAX_CHARS, and `sys_geo` is kept under
GIS_MAX_SYS_GEO_CHARS so the attribute row stays inside the metadata value limit.
"""

import json
import math
import os
import sqlite3
import time
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

from .common import (
    BranchResult,
    CLASS_DATA,
    DATA_CELL_MAX_CHARS,
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
# Wall-clock seconds of SQLite work one GeoPackage may take, and the share of it one layer's count may take.
GPKG_SQL_BUDGET_SECONDS = 120.0
GPKG_LAYER_BUDGET_SECONDS = 15.0
# SQLite virtual-machine instructions between two budget checks.
GPKG_PROGRESS_INTERVAL = 10_000
# The serialized `sys_geo` stays under this many characters; one metadata value may hold 400,000.
GIS_MAX_SYS_GEO_CHARS = 300_000
# Spatial references whose coordinates are WGS84 longitude/latitude, the only ones a footprint is written for.
WGS84_CRS_LABELS = frozenset({"EPSG:4326"})
# A CIM spatial reference wkid at or above this is an Esri authority code, not an EPSG one; ids end below the cap.
_ESRI_WKID_MIN = 100_000
_WKID_MAX = 10_000_000
_DESCRIPTION_MAX_CHARS = 500
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


# --- Shared -------------------------------------------------------------------------------------------------------


def _name(value) -> Optional[str]:
    """A name from the file as recorded: text cut to DATA_CELL_MAX_CHARS, or None when there is none."""
    if value is None or isinstance(value, (dict, list)):
        return None
    text = str(value)
    return text[:DATA_CELL_MAX_CHARS] if text else None


def _extent(row) -> Optional[List[float]]:
    """Four finite numbers, or None: an absent, non-numeric, infinite, NaN or overflowing value has no extent."""
    try:
        values = [float(value) for value in row]
    except (TypeError, ValueError, OverflowError):
        return None
    return values if all(math.isfinite(value) for value in values) else None


def _union_extent(extents: List[List[float]]) -> Optional[List[float]]:
    if not extents:
        return None
    return [min(e[0] for e in extents), min(e[1] for e in extents),
            max(e[2] for e in extents), max(e[3] for e in extents)]


def _wgs84_footprint(extent: Optional[List[float]]) -> Optional[dict]:
    """The GeoJSON footprint of a WGS84 extent: a Point for a degenerate box, otherwise a closed Polygon ring. None
    when the extent is not a longitude/latitude box (out of range, or min above max)."""
    if extent is None:
        return None
    min_x, min_y, max_x, max_y = extent
    if not (-180.0 <= min_x <= max_x <= 180.0 and -90.0 <= min_y <= max_y <= 90.0):
        return None
    if min_x == max_x and min_y == max_y:
        return {"type": "Point", "coordinates": [min_x, min_y]}
    ring = [[min_x, min_y], [max_x, min_y], [max_x, max_y], [min_x, max_y], [min_x, min_y]]
    return {"type": "Polygon", "coordinates": [ring]}


def _serialized_size(value) -> int:
    return len(json.dumps(value, sort_keys=True, default=str))


def _fit_sys_geo(sys_geo: dict, warnings: List[str]) -> None:
    """Keep the serialized `sys_geo` under GIS_MAX_SYS_GEO_CHARS: field lists are dropped from the last layer
    backwards, then layers themselves, and a warning says how many."""
    if _serialized_size(sys_geo) <= GIS_MAX_SYS_GEO_CHARS:
        return
    layers = sys_geo.get("layers") or []
    trimmed = 0
    for layer in reversed(layers):
        if "fields" in layer:
            del layer["fields"]
            trimmed += 1
            if _serialized_size(sys_geo) <= GIS_MAX_SYS_GEO_CHARS:
                break
    if trimmed:
        warnings.append(f"field names of {trimmed} layer(s) are left out to keep sys_geo under "
                        f"{GIS_MAX_SYS_GEO_CHARS:,} characters")
    dropped = 0
    while layers and _serialized_size(sys_geo) > GIS_MAX_SYS_GEO_CHARS:
        layers.pop()
        dropped += 1
    if dropped:
        warnings.append(f"{dropped} layer(s) are left out to keep sys_geo under {GIS_MAX_SYS_GEO_CHARS:,} characters")


# --- GeoPackage ---------------------------------------------------------------------------------------------------


class _SqlBudget:
    """An SQLite progress handler that interrupts the running statement once the file's budget, or the current
    layer's share of it, is spent. `LIMIT` bounds the rows a statement returns, not the work it does: a view whose
    query never yields a row would keep a bounded count running until the function stops."""

    def __init__(self, total_seconds: float):
        self._file_deadline = time.monotonic() + total_seconds
        self._deadline = self._file_deadline

    def start(self, seconds: Optional[float] = None) -> None:
        if seconds is None:
            self._deadline = self._file_deadline
        else:
            self._deadline = min(self._file_deadline, time.monotonic() + seconds)

    def spent(self) -> bool:
        return time.monotonic() > self._file_deadline

    def __call__(self) -> int:
        return 1 if time.monotonic() > self._deadline else 0


def _interrupted(exc: sqlite3.Error) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "interrupt" in str(exc).lower()


def _is_sqlite(path: str) -> bool:
    with open(path, "rb") as handle:
        return handle.read(len(_SQLITE_MAGIC)) == _SQLITE_MAGIC


def _object_type(connection: sqlite3.Connection, name: str) -> Optional[str]:
    """`table` or `view` for a schema object of that name, None when the file has none."""
    row = connection.execute(
        "SELECT type FROM sqlite_master WHERE name = ? AND type IN ('table', 'view') LIMIT 1", (name,)).fetchone()
    return str(row[0]) if row else None


def _placeholders(count: int) -> str:
    return ", ".join("?" for _ in range(count))


def _geometry_columns(connection: sqlite3.Connection, tables: List[str]) -> Dict[str, Tuple[str, str, object]]:
    """(geometry column, geometry type word, srs_id) per listed table, read for those tables only."""
    if not tables or _object_type(connection, "gpkg_geometry_columns") is None:
        return {}
    out: Dict[str, Tuple[str, str, object]] = {}
    for table_name, column_name, geometry_type, srs_id in connection.execute(
            "SELECT table_name, column_name, substr(geometry_type_name, 1, ?), srs_id FROM gpkg_geometry_columns "
            f"WHERE table_name IN ({_placeholders(len(tables))})", [DATA_CELL_MAX_CHARS, *tables]):
        out.setdefault(str(table_name), (str(column_name), str(geometry_type or "").upper(), srs_id))
    return out


def _srs_names(connection: sqlite3.Connection, srs_ids: Iterable[int]) -> Dict[int, dict]:
    """The spatial reference rows the listed layers name, read for those ids only."""
    ids = sorted({srs_id for srs_id in srs_ids if srs_id is not None})
    if not ids or _object_type(connection, "gpkg_spatial_ref_sys") is None:
        return {}
    out: Dict[int, dict] = {}
    for srs_id, organization, code in connection.execute(
            "SELECT srs_id, substr(organization, 1, ?), substr(organization_coordsys_id, 1, ?) "
            f"FROM gpkg_spatial_ref_sys WHERE srs_id IN ({_placeholders(len(ids))})",
            [DATA_CELL_MAX_CHARS, DATA_CELL_MAX_CHARS, *ids]):
        try:
            out[int(srs_id)] = {"organization": organization, "code": code}
        except (TypeError, ValueError, OverflowError):
            continue
    return out


def _int_or_none(value) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _crs_label(srs_id: Optional[int], srs: Dict[int, dict]) -> Optional[str]:
    """`EPSG:4326` for a registered authority code; the raw id when the registry does not name it. The GeoPackage
    spec reserves -1 (undefined Cartesian) and 0 (undefined geographic), registered under organization `NONE`;
    neither names a spatial reference."""
    if srs_id is None or srs_id in (-1, 0):
        return None
    entry = srs.get(srs_id)
    organization = _name(entry.get("organization")) if entry else None
    if organization and organization.upper() == "NONE":
        return None
    code = _name(entry.get("code")) if entry else None
    if organization and code is not None:
        return f"{organization.upper()}:{code}"
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
    return [_name(name) or "" for name in names if name != geometry_column][:DATA_MAX_COLUMNS]


def _geopackage_layers(connection: sqlite3.Connection, budget: _SqlBudget,
                       warnings: List[str]) -> Tuple[List[dict], int]:
    layer_total = int(connection.execute("SELECT COUNT(*) FROM gpkg_contents").fetchone()[0])
    # Text columns are cut inside SQLite, so an oversized value is never fetched whole; table_name is kept exact
    # because it names the table the layer reads.
    rows = connection.execute(
        "SELECT table_name, substr(data_type, 1, ?), substr(identifier, 1, ?), substr(description, 1, ?), "
        "min_x, min_y, max_x, max_y, srs_id FROM gpkg_contents ORDER BY table_name LIMIT ?",
        (DATA_CELL_MAX_CHARS, DATA_CELL_MAX_CHARS, _DESCRIPTION_MAX_CHARS + 1, GIS_MAX_LAYERS)).fetchall()
    table_names = [str(row[0]) for row in rows]
    geometry_columns = _geometry_columns(connection, table_names)
    srs_ids = [_int_or_none(row[8]) for row in rows] + [_int_or_none(entry[2]) for entry in geometry_columns.values()]
    srs = _srs_names(connection, srs_ids)
    layers: List[dict] = []
    views: List[str] = []
    uncounted = 0
    for (table_name, data_type, identifier, description, min_x, min_y, max_x, max_y, srs_id), table in zip(
            rows, table_names):
        geometry = geometry_columns.get(table)
        layer: Dict[str, object] = {
            "name": _name(identifier) or _name(table) or "",
            "table": _name(table) or "",
            "type": _name(data_type) or "unknown",
        }
        if description:
            layer["description"] = truncate_text(str(description), _DESCRIPTION_MAX_CHARS)
        layer_srs = _int_or_none(srs_id)
        if geometry is not None:
            word = geometry[1]
            layer["geometryType"] = _GPKG_GEOMETRY_TYPES.get(word, _name(word.title()) or "Geometry")
            if layer_srs is None:
                layer_srs = _int_or_none(geometry[2])
        crs = _crs_label(layer_srs, srs)
        if crs:
            layer["crs"] = crs
        layer["srsId"] = layer_srs
        extent = _extent((min_x, min_y, max_x, max_y))
        if extent is not None:
            layer["nativeExtent"] = extent
        if str(data_type) in ("features", "attributes"):
            if budget.spent():
                uncounted += 1
                layer["featureCountExact"] = False
                layers.append(layer)
                continue
            object_type = _object_type(connection, table)
            if object_type == "view":
                # A view's rows are the result of a query the file supplies; it is listed, not run.
                layer["view"] = True
                layer["featureCountExact"] = False
                views.append(layer["table"])
                try:
                    layer["fields"] = _columns(connection, table, geometry[0] if geometry else None)
                except sqlite3.Error as exc:
                    warnings.append(f"GeoPackage view {layer['table']!r} could not be described ({exc})")
            elif object_type == "table":
                budget.start(GPKG_LAYER_BUDGET_SECONDS)
                try:
                    count, capped = _bounded_count(connection, table)
                    layer["featureCount"] = count
                    layer["featureCountExact"] = not capped
                    layer["fields"] = _columns(connection, table, geometry[0] if geometry else None)
                except sqlite3.Error as exc:
                    layer["featureCountExact"] = False
                    if _interrupted(exc):
                        warnings.append(f"GeoPackage layer {layer['table']!r} was not counted: the read exceeded its "
                                        f"{GPKG_LAYER_BUDGET_SECONDS:g}-second budget")
                    else:
                        warnings.append(f"GeoPackage layer {layer['table']!r} could not be read ({exc})")
                finally:
                    budget.start()
        layers.append(layer)
    if views:
        warnings.append(f"{human_count(len(views), 'layer')} {'is a view' if len(views) == 1 else 'are views'}; "
                        "their rows are not counted")
    if uncounted:
        warnings.append(f"the GeoPackage read stopped after its {GPKG_SQL_BUDGET_SECONDS:g}-second budget; "
                        f"{human_count(uncounted, 'layer')} not counted")
    return layers, layer_total


def extract_geopackage(path: str, ctx: ExtractContext) -> BranchResult:
    if not _is_sqlite(path):
        return other_fallback(ctx, "GeoPackage is not an SQLite database")
    result = BranchResult(file_class=CLASS_DATA)
    uri = "file:" + os.path.abspath(path).replace("?", "%3f").replace("#", "%23") + "?mode=ro&immutable=1"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        return other_fallback(ctx, f"GeoPackage could not be opened: {exc}")
    budget = _SqlBudget(GPKG_SQL_BUDGET_SECONDS)
    try:
        connection.set_progress_handler(budget, GPKG_PROGRESS_INTERVAL)
        if _object_type(connection, "gpkg_contents") is None:
            return other_fallback(ctx, "SQLite database carries no gpkg_contents table; not a GeoPackage")
        layers, layer_total = _geopackage_layers(connection, budget, result.warnings)
    except sqlite3.Error as exc:
        if _interrupted(exc):
            return other_fallback(
                ctx, f"GeoPackage metadata was not read within its {GPKG_SQL_BUDGET_SECONDS:g}-second budget")
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
                     if layer.get("crs") in WGS84_CRS_LABELS and "nativeExtent" in layer]
    footprint = _wgs84_footprint(_union_extent(wgs84_extents))
    if wgs84_extents and footprint is None:
        result.warnings.append("the EPSG:4326 layer extents are not a longitude/latitude box; no footprint is written")
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
    _fit_sys_geo(sys_geo, result.warnings)
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
                    if key in layer} for layer in sys_geo["layers"]],
    }
    result.text_excerpt = truncate_text(json.dumps(summary, default=str), ctx.max_text_chars)
    return result


# --- ArcGIS Pro layer file (.lyrx) ------------------------------------------------------------------------------


def _walk_layer_definitions(document: dict) -> List[dict]:
    definitions = document.get("layerDefinitions")
    return [entry for entry in definitions if isinstance(entry, dict)] if isinstance(definitions, list) else []


# Keys of a `workspaceConnectionString` (an `;`-separated `KEY=value` list) that identify where the data lives.
# Every other pair is dropped, so a credential under any key name is never recorded.
_CONNECTION_KEPT_KEYS = frozenset({
    "SERVER", "INSTANCE", "DATABASE", "VERSION", "BRANCH", "DBCLIENT", "DB_CONNECTION_PROPERTIES", "URL",
})


def _scrub_url(value: str) -> str:
    """A URL without anything a client could authenticate with: the user-info, the query string (a service token is
    commonly carried in `?token=`) and the fragment are dropped. A value that is not a URL keeps its text up to any
    `?` or `#`. An unparseable URL is dropped."""
    value = value.strip()
    if "://" not in value:
        return value.split("?", 1)[0].split("#", 1)[0]
    try:
        parts = urlsplit(value)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return ""
    netloc = (f"[{host}]" if ":" in host else host) + (f":{port}" if port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _scrub_connection_string(value: str) -> str:
    """The connection string reduced to the pairs that say where the data lives (_CONNECTION_KEPT_KEYS), with any
    URL value scrubbed; pairs under any other key, and text without a `KEY=` prefix, are dropped."""
    kept = []
    for pair in value.split(";"):
        key, separator, item = pair.partition("=")
        normalized = key.strip().upper()
        if not separator or normalized not in _CONNECTION_KEPT_KEYS:
            continue
        if normalized == "URL":
            item = _scrub_url(item)
            if not item:
                continue
            pair = f"{key}={item}"
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
        if not isinstance(value, str) or not value:
            continue
        if source_key == "workspaceConnectionString":
            value = _scrub_connection_string(value)
        elif source_key == "url":
            value = _scrub_url(value)
        if value:
            out[target_key] = truncate_text(value, 500)
    return out


def _display_fields(definition: dict) -> List[str]:
    """The fields the layer shows: its `fieldDescriptions` entries not marked `visible: false`."""
    table = definition.get("featureTable") if isinstance(definition.get("featureTable"), dict) else None
    fields = table.get("fieldDescriptions") if isinstance(table, dict) else None
    names: List[str] = []
    for entry in fields if isinstance(fields, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("fieldName"), str) and entry.get("visible") is not False:
            names.append(_name(entry["fieldName"]) or "")
    return names[:DATA_MAX_COLUMNS]


def _renderer_type(definition: dict) -> Optional[str]:
    renderer = definition.get("renderer")
    if isinstance(renderer, dict) and isinstance(renderer.get("type"), str):
        return _name(renderer["type"])
    return None


def _wkid_label(wkid) -> Optional[str]:
    """`EPSG:<wkid>`, or `ESRI:<wkid>` for an Esri authority code (100000 and above); None for anything that is not
    a positive integer id (a boolean, a string, an absurd number)."""
    if isinstance(wkid, bool) or not isinstance(wkid, int) or not 0 < wkid < _WKID_MAX:
        return None
    return f"{'ESRI' if wkid >= _ESRI_WKID_MIN else 'EPSG'}:{wkid}"


def _extent_from_cim(definition: dict) -> Tuple[Optional[List[float]], Optional[str]]:
    """A CIM layer rarely stores its extent; when it does, as `{xmin, ymin, xmax, ymax, spatialReference}`, the
    extent and the label of the spatial reference's `latestWkid` (or `wkid`) are returned."""
    for key in ("extent", "customFullExtent"):
        extent = definition.get(key)
        if isinstance(extent, dict) and all(k in extent for k in ("xmin", "ymin", "xmax", "ymax")):
            values = _extent((extent["xmin"], extent["ymin"], extent["xmax"], extent["ymax"]))
            reference = extent.get("spatialReference")
            label = None
            if isinstance(reference, dict):
                label = _wkid_label(reference.get("latestWkid")) or _wkid_label(reference.get("wkid"))
            return values, label
    return None, None


def _load_layer_document(path: str):
    with open(path, "rb") as handle:
        return json.loads(handle.read().decode("utf-8-sig"))


def extract_lyrx(path: str, ctx: ExtractContext) -> BranchResult:
    size = os.path.getsize(path)
    if size > TEXT_HEAD_BYTES:
        return other_fallback(ctx, f"layer file is {size} bytes, above the {TEXT_HEAD_BYTES}-byte bound for a document")
    try:
        document = _load_layer_document(path)
    except RecursionError:
        return other_fallback(ctx, "layer file nests deeper than the JSON parser reads")
    except (UnicodeDecodeError, ValueError) as exc:
        return other_fallback(ctx, f"layer file is not a JSON document ({exc})")
    if not isinstance(document, dict) or document.get("type") != "CIMLayerDocument":
        return other_fallback(ctx, "JSON document is not a CIMLayerDocument")
    result = BranchResult(file_class=CLASS_DATA)
    layers: List[dict] = []
    footprint = None
    crs_values: List[str] = []
    definitions = _walk_layer_definitions(document)
    for definition in definitions[:GIS_MAX_LAYERS]:
        cim_type = _name(definition.get("type")) or ""
        layer: Dict[str, object] = {
            "name": _name(definition.get("name")) or _name(definition.get("uRI")) or cim_type,
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
            if crs in WGS84_CRS_LABELS and extent is not None and footprint is None:
                footprint = _wgs84_footprint(extent)
                if footprint is None:
                    result.warnings.append(f"layer {layer['name']!r}: the EPSG:4326 extent is not a "
                                           "longitude/latitude box; no footprint is written")
        layers.append(layer)
    if len(definitions) > GIS_MAX_LAYERS:
        result.warnings.append(f"layer file lists {len(definitions)} layers; the first {GIS_MAX_LAYERS} are recorded")
    if not layers:
        result.warnings.append("layer file carries no layer definitions")
    kinds = sorted({str(layer["type"]) for layer in layers})
    sources = sorted({_name(layer["source"]["dataset"]) or "" for layer in layers
                      if isinstance(layer.get("source"), dict) and layer["source"].get("dataset")})
    unique_crs = sorted(set(crs_values))
    # A layer file names its data but does not carry it, so it records no feature count or geometry types.
    sys_geo: Dict[str, object] = {
        "format": _LYRX_FORMAT,
        "type": "CIMLayerDocument",
        "version": _name(document.get("version")),
        "layerCount": len(definitions),
        "layers": layers,
        "layerKinds": kinds,
        "datasets": sources[:GIS_MAX_LAYERS],
        "crs": unique_crs[0] if len(unique_crs) == 1 else unique_crs,
        "footprint": footprint,
    }
    _fit_sys_geo(sys_geo, result.warnings)
    result.attributes["sys_geo"] = sys_geo
    result.facts["layers"] = human_count(len(definitions), "layer")
    if kinds:
        result.facts["layerKinds"] = ", ".join(kinds)
    if sources:
        result.facts["datasets"] = ", ".join(sources[:5])
    if footprint is not None:
        result.facts["footprint"] = str(footprint["type"]) + " bounding box"
    result.warnings.append(
        "a layer file references its data by name; the dataset itself is not in this file")
    summary = {"format": _LYRX_FORMAT, "layers": [
        {key: layer[key] for key in ("name", "type", "source", "fields", "renderer") if key in layer}
        for layer in sys_geo["layers"]]}
    result.text_excerpt = truncate_text(json.dumps(summary, default=str), ctx.max_text_chars)
    return result
