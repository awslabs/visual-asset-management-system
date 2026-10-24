# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""``sys_*`` attribute extraction for the analysis manifest.

Every extractor returns ``{sys_key: dict}`` of plain JSON values: the metadata step stringifies each
``sys_*`` value into one string-only file attribute, promotes the keys named in ``PROMOTION_SOURCE_KEYS``
to typed ``ext_*`` metadata, and composes embedding text from the ``facts`` sentences built here. A
promotion group carries exactly its contract keys (``None`` when a value is unknown); everything else a
reader learns about the file lives under ``sys_format.details``. Format libraries are imported inside
the function that needs them."""

import math
import os
import re
import shutil
import zipfile
from collections import Counter
from typing import Dict, FrozenSet, Optional, Tuple

import numpy as np

from ..format_handlers import cad_handler, pcd_reader
from . import headers

SYS_GEOMETRY = "sys_geometry"
SYS_STATISTICS = "sys_statistics"
SYS_FORMAT = "sys_format"
SYS_VISUAL = "sys_visual"
SYS_SCENE = "sys_scene"
SYS_CAD = "sys_cad"
SYS_POINTCLOUD = "sys_pointcloud"
SYS_IFC = "sys_ifc"

# The attribute keys the metadata step promotes to typed ext_* metadata, per group.
PROMOTION_SOURCE_KEYS: Dict[str, FrozenSet[str]] = {
    SYS_GEOMETRY: frozenset({"boundsMin", "boundsMax", "dimensions", "extentMax", "volume", "surfaceArea",
                             "units"}),
    SYS_STATISTICS: frozenset({"meshCount", "vertices", "faces", "triangles", "watertight"}),
    SYS_VISUAL: frozenset({"materialCount", "textureCount", "hasUv", "hasVertexColors"}),
    SYS_SCENE: frozenset({"nodeCount", "hasAnimation", "hasArmature"}),
    SYS_CAD: frozenset({"solidCount", "faceCount", "edgeCount", "assemblyCount", "declaredUnits",
                        "estimatedUnits"}),
    SYS_POINTCLOUD: frozenset({"pointCount", "hasColor", "boundsMin", "boundsMax", "crs"}),
    SYS_IFC: frozenset({"schema", "projectName", "storeyCount", "elementCount", "site"}),
}

# Length units the metadata step understands; any other unit is reported as unknown (None).
UNIT_TOKENS = ("m", "mm", "cm", "in", "ft")
_UNIT_ALIASES = {
    "m": "m", "metre": "m", "meter": "m", "metres": "m", "meters": "m",
    "mm": "mm", "millimetre": "mm", "millimeter": "mm", "millimetres": "mm", "millimeters": "mm",
    "cm": "cm", "centimetre": "cm", "centimeter": "cm", "centimetres": "cm", "centimeters": "cm",
    "in": "in", "inch": "in", "inches": "in",
    "ft": "ft", "foot": "ft", "feet": "ft",
}
# USD stage metersPerUnit values that name one of the unit tokens.
_METERS_PER_UNIT_TOKENS = ((1.0, "m"), (0.001, "mm"), (0.01, "cm"), (0.0254, "in"), (0.3048, "ft"))
# glTF defines its scene units as metres.
_METRE_EXTENSIONS = {".glb", ".gltf"}

# GeoTIFF key ids carried by a LAS GeoKeyDirectory VLR; values 1024..32766 are EPSG codes.
GEOGRAPHIC_TYPE_GEOKEY = 2048
PROJECTED_CS_TYPE_GEOKEY = 3072
_EPSG_RANGE = range(1024, 32767)
# Root node of a WKT string, ``KEYWORD["name",...``; a name escapes an embedded quote by doubling it.
_WKT_ROOT = re.compile(r'^\s*([A-Za-z_]+)\s*\[\s*"((?:[^"]|"")*)"')
_WKT_GEOGRAPHIC_ROOTS = {"GEOGCS", "GEOGCRS", "GEODCRS", "GEOGRAPHICCRS", "GEODETICCRS"}

# IFC entity types counted individually in the details; ``elementCount`` counts every IfcElement.
IFC_COUNTED_TYPES = (
    "IfcWall", "IfcSlab", "IfcBeam", "IfcColumn", "IfcDoor", "IfcWindow", "IfcStair", "IfcRoof",
    "IfcSpace", "IfcBuildingStorey", "IfcFurnishingElement", "IfcPipeSegment", "IfcDuctSegment",
)

# DXF $INSUNITS header codes, and the codes that name a unit token.
DXF_INSUNITS = {
    0: "unitless", 1: "inches", 2: "feet", 3: "miles", 4: "millimeters", 5: "centimeters", 6: "meters",
    7: "kilometers", 8: "microinches", 9: "mils", 10: "yards", 11: "angstroms", 12: "nanometers",
    13: "microns", 14: "decimeters", 15: "decameters", 16: "hectometers", 17: "gigameters",
    18: "astronomical units", 19: "light years", 20: "parsecs",
}
DXF_INSUNITS_TOKENS = {1: "in", 2: "ft", 4: "mm", 5: "cm", 6: "m"}
# DXF entity types counted as solids, faces, edges (curves) and assemblies (block references).
_DXF_SOLID_TYPES = {"3DSOLID"}
_DXF_FACE_TYPES = {"3DFACE"}
_DXF_EDGE_TYPES = {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE"}


def json_safe(value):
    """Recursively convert numpy scalars/arrays and tuples/sets into plain JSON-serialisable values."""
    if isinstance(value, dict):
        return {str(json_safe(k)): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    if isinstance(value, np.ndarray):
        return [json_safe(v) for v in value.tolist()]
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, float):
        return float(value)
    return value


def normalize_unit(name) -> Optional[str]:
    """One of ``UNIT_TOKENS`` for a unit name in any common spelling (``millimetre``, ``meters (estimated)``,
    ``inch`` ...), or None when the name is empty or names no supported unit."""
    if not name:
        return None
    token = str(name).lower().replace("(estimated)", "").strip(" .")
    return _UNIT_ALIASES.get(token)


def merge_groups(base: dict, extra: dict) -> dict:
    """Merge attribute groups key by key into ``base`` (returned); ``details`` dicts merge one level deep."""
    for group, values in extra.items():
        target = base.setdefault(group, {})
        for key, value in values.items():
            if key == "details" and isinstance(value, dict) and isinstance(target.get("details"), dict):
                target["details"].update(value)
            else:
                target[key] = value
    return base


def geometry_group(bounds_min, bounds_max, volume=None, surface_area=None, units=None) -> dict:
    """The ``sys_geometry`` contract keys from a pair of bounds (both None when there is no geometry)."""
    if bounds_min is None or bounds_max is None:
        return {"boundsMin": None, "boundsMax": None, "dimensions": None, "extentMax": None,
                "volume": volume, "surfaceArea": surface_area, "units": units}
    lo = [float(v) for v in bounds_min]
    hi = [float(v) for v in bounds_max]
    size = [b - a for a, b in zip(lo, hi)]
    return {"boundsMin": lo, "boundsMax": hi,
            "dimensions": {"width": size[0], "height": size[1], "depth": size[2]},
            "extentMax": max(size), "volume": volume, "surfaceArea": surface_area, "units": units}


def geometry_from_points(points, units=None) -> dict:
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if len(pts) == 0:
        return geometry_group(None, None, units=units)
    return geometry_group(pts.min(axis=0), pts.max(axis=0), units=units)


def _optional_float(value) -> Optional[float]:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def polydata_attributes(pv_data, ext: str, loader: str) -> dict:
    """``sys_geometry``, ``sys_statistics``, ``sys_visual`` and ``sys_format`` for any loaded PolyData."""
    points = np.asarray(pv_data.points, dtype=np.float64).reshape(-1, 3)
    n_points = int(len(points))
    n_cells = int(getattr(pv_data, "n_cells", 0) or 0)
    is_point_cloud = n_cells == 0 or n_cells == n_points
    faces = 0 if is_point_cloud else n_cells
    all_triangles = bool(getattr(pv_data, "is_all_triangles", True))

    # Open-edge count and measures are PolyData properties; a point set or a test double offers none.
    open_edges = getattr(pv_data, "n_open_edges", None) if faces else None
    watertight = None if open_edges is None else int(open_edges) == 0
    volume = _optional_float(getattr(pv_data, "volume", None)) if watertight else None
    surface_area = _optional_float(getattr(pv_data, "area", None)) if faces else None
    geometry = geometry_from_points(points, units="m" if ext in _METRE_EXTENSIONS else None)
    geometry["volume"] = volume
    geometry["surfaceArea"] = surface_area

    point_data = getattr(pv_data, "point_data", None)
    has_colors = point_data is not None and "RGB" in point_data
    texture_count = 1 if getattr(pv_data, "_preview_texture", None) is not None else 0
    return {
        SYS_GEOMETRY: geometry,
        SYS_STATISTICS: {"meshCount": 1 if faces else 0, "vertices": n_points, "faces": faces,
                         "triangles": faces if all_triangles else None, "watertight": watertight},
        # A merged PolyData carries no material list: the count is unknown rather than zero.
        SYS_VISUAL: {"materialCount": None, "textureCount": texture_count,
                     "hasUv": getattr(pv_data, "active_texture_coordinates", None) is not None,
                     "hasVertexColors": bool(has_colors)},
        SYS_FORMAT: {"extension": ext, "loader": loader, "details": {}},
    }


def pointcloud_header(ext: str, file_path: str) -> dict:
    """Header facts for a point-cloud file, read without loading its points: ``points``, ``boundsMin``,
    ``boundsMax`` and ``crs`` when the header carries them, everything else under ``details``."""
    if ext in (".las", ".laz"):
        return las_header(file_path)
    if ext == ".e57":
        return e57_header(file_path)
    if ext == ".pcd":
        header = pcd_reader.read_pcd_header(file_path)
        return {"points": header["points"],
                "details": {"version": header["version"], "fields": header["fields"], "data": header["data"],
                            "width": header["width"], "height": header["height"]}}
    if ext == ".ptx":
        header = dict(headers.read_ptx_header(file_path))
        return {"points": header.pop("points", None), "details": header}
    return {}


def wkt_root(wkt: str) -> Tuple[Optional[str], Optional[str]]:
    """``(keyword, name)`` of a WKT string's root node, e.g. ``("PROJCS", "WGS 84 / UTM zone 33N")``."""
    match = _WKT_ROOT.match(wkt or "")
    if not match:
        return None, None
    return match.group(1).upper(), match.group(2).replace('""', '"')


def crs_from_las_vlrs(vlrs) -> Optional[dict]:
    """``{epsg, name, geographic}`` from the LAS header's CRS records, or None when it carries none.

    The GeoTIFF key directory supplies the EPSG code (``ProjectedCSTypeGeoKey`` wins over a redundant
    ``GeographicTypeGeoKey``); the OGC WKT record supplies the name, and its root keyword decides
    ``geographic`` when no key directory did. Keys whose value lives in another VLR are skipped."""
    epsg = None
    name = None
    geographic = None
    found = False
    for vlr in vlrs or []:
        kind = type(vlr).__name__
        if kind == "GeoKeyDirectoryVlr":
            found = True
            projected = geographic_code = None
            for key in getattr(vlr, "geo_keys", None) or []:
                if int(key.tiff_tag_location) != 0 or int(key.value_offset) not in _EPSG_RANGE:
                    continue
                if int(key.id) == PROJECTED_CS_TYPE_GEOKEY:
                    projected = int(key.value_offset)
                elif int(key.id) == GEOGRAPHIC_TYPE_GEOKEY:
                    geographic_code = int(key.value_offset)
            if projected is not None:
                epsg, geographic = projected, False
            elif geographic_code is not None:
                epsg, geographic = geographic_code, True
        elif kind == "WktCoordinateSystemVlr":
            found = True
            root, root_name = wkt_root(getattr(vlr, "string", "") or "")
            if root_name:
                name = root_name
            if geographic is None and root:
                geographic = root in _WKT_GEOGRAPHIC_ROOTS
    if not found:
        return None
    return {"epsg": epsg, "name": name, "geographic": bool(geographic)}


def las_header(file_path: str) -> dict:
    import laspy

    with laspy.open(file_path) as reader:
        header = reader.header
        dims = list(header.point_format.dimension_names)
        vlrs = list(header.vlrs)
        return {
            "points": int(header.point_count),
            "boundsMin": [float(v) for v in header.mins],
            "boundsMax": [float(v) for v in header.maxs],
            "crs": crs_from_las_vlrs(vlrs),
            "details": {
                "version": f"{header.version.major}.{header.version.minor}",
                "pointFormat": int(header.point_format.id),
                "scales": [float(v) for v in header.scales],
                "offsets": [float(v) for v in header.offsets],
                "hasColorDimensions": all(name in dims for name in ("red", "green", "blue")),
                "hasGpsTime": "gps_time" in dims,
                "vlrCount": len(vlrs),
                "vlrTypes": [type(vlr).__name__ for vlr in vlrs],
                "generatingSoftware": str(header.generating_software or "").strip("\x00 "),
                "systemIdentifier": str(header.system_identifier or "").strip("\x00 "),
            },
        }


def e57_header(file_path: str) -> dict:
    """E57 carries a pose per scan and no CRS record this image reads, so ``crs`` is None."""
    import pye57

    e57 = pye57.E57(file_path)
    scan_count = int(e57.scan_count)
    result = {"points": None, "crs": None, "details": {"scanCount": scan_count}}
    if scan_count:
        header = e57.get_header(0)
        result["points"] = int(header.point_count)
        fields = getattr(header, "point_fields", None) or getattr(header, "scan_fields", None) or []
        result["details"]["scanFields"] = [str(f) for f in fields]
    return result


def pointcloud_attributes(ext: str, points, colors, header: dict, loader: str) -> dict:
    """``sys_pointcloud`` from the loaded (possibly sampled) points plus the header facts, which win for
    the count and the bounds because they describe the whole file; the sampled count and every other
    header fact go to ``sys_format.details``."""
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    header = dict(header or {})
    details = dict(header.pop("details", None) or {})
    bounds = geometry_from_points(pts)
    cloud = {
        "pointCount": int(header.pop("points", None) or len(pts)),
        "hasColor": bool(colors is not None and len(colors) > 0),
        "boundsMin": header.pop("boundsMin", None) or bounds["boundsMin"],
        "boundsMax": header.pop("boundsMax", None) or bounds["boundsMax"],
        "crs": header.pop("crs", None),
    }
    details.update(header)
    details["pointsLoaded"] = int(len(pts))
    return {SYS_POINTCLOUD: cloud, SYS_FORMAT: {"extension": ext, "loader": loader, "details": details}}


def usd_scene_attributes(file_path: str) -> dict:
    """Partial groups from the USD stage, merged over the tessellated mesh's groups with ``merge_groups``:
    ``sys_scene``, the mesh count, the stage units and the stage facts under ``sys_format.details``."""
    from pxr import Usd, UsdGeom

    stage = Usd.Stage.Open(file_path)
    if stage is None:
        raise ValueError(f"Failed to open USD stage: {file_path}")

    type_counts = Counter()
    total = 0
    for prim in stage.Traverse():
        total += 1
        type_counts[str(prim.GetTypeName()) or "untyped"] += 1

    default_prim = stage.GetDefaultPrim()
    root_layer = stage.GetRootLayer()
    start, end = float(stage.GetStartTimeCode()), float(stage.GetEndTimeCode())
    has_authored_timecodes = bool(stage.HasAuthoredTimeCodeRange())
    meters_per_unit = float(UsdGeom.GetStageMetersPerUnit(stage))
    units = next((token for value, token in _METERS_PER_UNIT_TOKENS
                  if math.isclose(meters_per_unit, value, rel_tol=1e-6)), None)
    return {
        SYS_SCENE: {
            "nodeCount": total,
            "hasAnimation": (has_authored_timecodes and end > start) or type_counts.get("SkelAnimation", 0) > 0,
            "hasArmature": type_counts.get("Skeleton", 0) > 0 or type_counts.get("SkelRoot", 0) > 0,
        },
        SYS_STATISTICS: {"meshCount": int(type_counts.get("Mesh", 0))},
        SYS_GEOMETRY: {"units": units},
        SYS_FORMAT: {"details": {
            "primTypeCounts": dict(type_counts),
            "upAxis": str(UsdGeom.GetStageUpAxis(stage)),
            "metersPerUnit": meters_per_unit,
            "defaultPrim": default_prim.GetName() if default_prim and default_prim.IsValid() else None,
            "subLayerCount": len(root_layer.subLayerPaths) if root_layer is not None else 0,
            "startTimeCode": start,
            "endTimeCode": end,
            "hasAuthoredTimeCodes": has_authored_timecodes,
        }},
    }


def dxf_attributes(file_path: str) -> dict:
    import ezdxf
    from ezdxf import bbox

    doc = ezdxf.readfile(file_path)
    modelspace = doc.modelspace()
    entity_counts = Counter(entity.dxftype() for entity in modelspace)

    insunits = int(doc.header.get("$INSUNITS", 0) or 0)
    units = DXF_INSUNITS_TOKENS.get(insunits)
    details = {
        "is2d": True,
        "dxfVersion": str(doc.dxfversion),
        "insunitsCode": insunits,
        "insunitsName": DXF_INSUNITS.get(insunits, f"code {insunits}"),
        "layers": len(doc.layers),
        "blocks": len(doc.blocks),
        "entityCounts": dict(entity_counts),
        "entityTotal": int(sum(entity_counts.values())),
    }
    extents = bbox.extents(modelspace, fast=True)
    if getattr(extents, "has_data", False):
        lo, hi = extents.extmin, extents.extmax
        geometry = geometry_group([lo.x, lo.y, 0.0], [hi.x, hi.y, 0.0], units=units)
        details["area"] = float(hi.x - lo.x) * float(hi.y - lo.y)
    else:
        geometry = geometry_group(None, None, units=units)

    cad = {
        "solidCount": int(sum(entity_counts[name] for name in _DXF_SOLID_TYPES)),
        "faceCount": int(sum(entity_counts[name] for name in _DXF_FACE_TYPES)),
        "edgeCount": int(sum(entity_counts[name] for name in _DXF_EDGE_TYPES)),
        "assemblyCount": int(entity_counts.get("INSERT", 0)),
        "declaredUnits": units,
        "estimatedUnits": None,
    }
    return {SYS_GEOMETRY: geometry, SYS_CAD: cad,
            SYS_FORMAT: {"extension": ".dxf", "loader": "ezdxf", "details": details}}


def _unzip_first_member(archive_path: str, work_dir: str, suffix: str) -> str:
    """Extract the first member ending in ``suffix`` into ``work_dir`` (by basename, so member paths
    cannot escape it) and return its path."""
    with zipfile.ZipFile(archive_path) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith(suffix) and not n.endswith("/")]
        if not names:
            raise ValueError(f"{os.path.basename(archive_path)} contains no {suffix} member")
        target = os.path.join(work_dir, os.path.basename(names[0]))
        with archive.open(names[0]) as source, open(target, "wb") as destination:
            shutil.copyfileobj(source, destination)
    return target


def dms_to_decimal(components) -> Optional[float]:
    """Decimal degrees from an IFC compound plane angle — degrees, minutes, seconds and optionally
    millionths of a second, every component carrying the sign — or None when the value is not one."""
    if not isinstance(components, (list, tuple)) or len(components) not in (3, 4):
        return None
    try:
        parts = [int(component) for component in components]
    except (TypeError, ValueError):
        return None
    parts += [0] * (4 - len(parts))
    negative = any(part < 0 for part in parts)
    degrees, minutes, seconds, millionths = (abs(part) for part in parts)
    value = degrees + minutes / 60.0 + seconds / 3600.0 + millionths / 3_600_000_000.0
    return -value if negative else value


def ifc_site(model) -> Optional[dict]:
    """``{latitude, longitude, elevation}`` from the first IfcSite that carries a reference position."""
    for site in model.by_type("IfcSite"):
        latitude = dms_to_decimal(getattr(site, "RefLatitude", None))
        longitude = dms_to_decimal(getattr(site, "RefLongitude", None))
        if latitude is None or longitude is None or abs(latitude) > 90 or abs(longitude) > 180:
            continue
        elevation = getattr(site, "RefElevation", None)
        return {"latitude": latitude, "longitude": longitude,
                "elevation": None if elevation is None else float(elevation)}
    return None


def ifc_attributes(file_path: str, work_dir: str) -> dict:
    import ifcopenshell

    path = file_path
    ext = ".ifczip" if file_path.lower().endswith(".ifczip") else ".ifc"
    if ext == ".ifczip":
        path = _unzip_first_member(file_path, work_dir, ".ifc")
    model = ifcopenshell.open(path)

    projects = model.by_type("IfcProject")
    project_name = getattr(projects[0], "Name", None) if projects else None
    counts = {name: len(model.by_type(name)) for name in IFC_COUNTED_TYPES}
    ifc = {
        "schema": str(model.schema),
        "projectName": project_name or None,
        "storeyCount": len(model.by_type("IfcBuildingStorey")),
        "elementCount": len(model.by_type("IfcElement")),
        "site": ifc_site(model),
    }
    details = {
        "sites": len(model.by_type("IfcSite")),
        "buildings": len(model.by_type("IfcBuilding")),
        "products": len(model.by_type("IfcProduct")),
        "entityCounts": {name: count for name, count in counts.items() if count},
    }
    return {SYS_IFC: ifc, SYS_FORMAT: {"extension": ext, "loader": "ifcopenshell", "details": details}}


def splat_attributes(ext: str, file_path: str) -> dict:
    """``sys_pointcloud`` for a Gaussian-splat container from its header alone: the splat count is the
    point count, every splat format stores a colour per splat, and a header carries neither bounds nor
    a CRS."""
    if ext == ".ply":
        header = headers.read_ply_header(file_path)
        vertex = header["elements"].get("vertex") or {}
        properties = vertex.get("properties", [])
        points = int(vertex.get("count", 0))
        details = {"container": "ply", "plyFormat": header["format"], "properties": len(properties),
                   "shDegree": headers.ply_sh_degree(properties)}
    elif ext == ".splat":
        header = dict(headers.read_splat_header(file_path))
        points = int(header.pop("points", 0))
        details = {"container": "splat", **header}
    elif ext == ".spz":
        header = dict(headers.read_spz_header(file_path))
        points = int(header.pop("points", 0))
        details = {"container": "spz", **header}
    elif ext == ".sog":
        header = dict(headers.read_sog_meta(file_path))
        points = int(header.pop("points", 0))
        details = {"container": "sog", **header}
    else:
        raise ValueError(f"Unsupported splat container: {ext}")
    details["isGaussianSplat"] = True
    return {
        SYS_POINTCLOUD: {"pointCount": points, "hasColor": True, "boundsMin": None, "boundsMax": None,
                         "crs": None},
        SYS_FORMAT: {"extension": ext, "loader": "header", "details": details},
    }


def cad_shape_attributes(shape, ext: str, file_path: str, pv_data=None) -> dict:
    """``sys_geometry``, ``sys_statistics``, ``sys_cad`` and ``sys_format`` for an OCP shape; the
    tessellation counts are added when the rendered PolyData is supplied."""
    statistics = dict(cad_handler.shape_statistics(shape))
    measured = dict(cad_handler.shape_geometry(shape))
    max_dimension = max(measured["dimensions"]) if measured.get("dimensions") else 0.0

    declared_name = cad_handler.declared_step_units(file_path) if ext in (".stp", ".step") else None
    estimated_name = cad_handler.estimated_units(max_dimension)
    declared = normalize_unit(declared_name)
    estimated = normalize_unit(estimated_name)
    geometry = geometry_group(measured["boundsMin"], measured["boundsMax"], volume=measured.get("volume"),
                              surface_area=measured.get("surfaceArea"), units=declared or estimated)
    solids = int(statistics.get("solids", 0))
    cad = {
        "solidCount": solids,
        "faceCount": int(statistics.get("faces", 0)),
        "edgeCount": int(statistics.get("edges", 0)),
        "assemblyCount": solids,
        "declaredUnits": declared,
        "estimatedUnits": estimated,
    }
    details = {
        "source": ext.lstrip("."),
        "isAssembly": solids > 1,
        "shells": int(statistics.get("shells", 0)),
        "wires": int(statistics.get("wires", 0)),
        "centerOfMass": measured.get("centerOfMass"),
        "declaredUnitName": declared_name,
        "estimatedUnitName": estimated_name,
    }
    triangles = None
    if pv_data is not None:
        tessellated = polydata_attributes(pv_data, ext, "OCP")[SYS_STATISTICS]
        details["tessellatedVertices"] = tessellated["vertices"]
        details["tessellatedFaces"] = tessellated["faces"]
        triangles = tessellated["triangles"]
    stats = {"meshCount": solids, "vertices": int(statistics.get("vertices", 0)),
             "faces": int(statistics.get("faces", 0)), "triangles": triangles, "watertight": None}
    return {SYS_GEOMETRY: geometry, SYS_STATISTICS: stats, SYS_CAD: cad,
            SYS_FORMAT: {"extension": ext, "loader": "OCP", "details": details}}


def _count(value) -> int:
    return int(value or 0)


def facts_for(attributes: dict) -> dict:
    """One human-readable sentence per topic, composed from the attributes present."""
    facts = {}
    geometry = attributes.get(SYS_GEOMETRY) or {}
    statistics = attributes.get(SYS_STATISTICS) or {}
    details = (attributes.get(SYS_FORMAT) or {}).get("details") or {}

    dimensions = geometry.get("dimensions")
    if dimensions:
        dims = " x ".join(f"{float(dimensions[axis]):,.3f}" for axis in ("width", "height", "depth"))
        sentence = f"bounding box {dims} {geometry.get('units') or 'units'}"
        if statistics.get("vertices") is not None:
            sentence += (f"; {_count(statistics.get('vertices')):,} vertices, "
                         f"{_count(statistics.get('faces')):,} faces")
        facts["geometry"] = sentence

    cloud = attributes.get(SYS_POINTCLOUD) or {}
    if cloud:
        if details.get("isGaussianSplat"):
            facts["pointcloud"] = (f"Gaussian splat with {_count(cloud.get('pointCount')):,} splats in a "
                                   f"{details.get('container')} container")
        else:
            colour = "with colour" if cloud.get("hasColor") else "without colour"
            sentence = f"{_count(cloud.get('pointCount')):,} points, {colour}"
            crs = cloud.get("crs") or {}
            if crs.get("epsg") is not None:
                sentence += f", CRS EPSG:{crs['epsg']}"
                if crs.get("name"):
                    sentence += f" ({crs['name']})"
            elif crs.get("name"):
                sentence += f", CRS {crs['name']}"
            facts["pointcloud"] = sentence

    cad = attributes.get(SYS_CAD) or {}
    if cad:
        units = cad.get("declaredUnits") or cad.get("estimatedUnits") or "unknown"
        if details.get("is2d"):
            facts["cad"] = (f"2D DXF drawing ({details.get('dxfVersion')}), units {units}, "
                            f"{_count(details.get('entityTotal')):,} entities on "
                            f"{_count(details.get('layers'))} layers")
        else:
            facts["cad"] = (f"B-rep model with {_count(cad.get('solidCount'))} solids, "
                            f"{_count(cad.get('faceCount')):,} faces, {_count(cad.get('edgeCount')):,} edges; "
                            f"units {units}")

    scene = attributes.get(SYS_SCENE) or {}
    if scene:
        facts["scene"] = (f"USD stage with {_count(scene.get('nodeCount')):,} prims "
                          f"({_count(statistics.get('meshCount'))} meshes), {details.get('upAxis')}-up, "
                          f"{details.get('metersPerUnit')} m per unit")

    ifc = attributes.get(SYS_IFC) or {}
    if ifc:
        sentence = f"{ifc.get('schema')} model"
        if ifc.get("projectName"):
            sentence += f" '{ifc['projectName']}'"
        sentence += (f" with {_count(ifc.get('elementCount')):,} elements across "
                     f"{_count(ifc.get('storeyCount'))} storeys")
        site = ifc.get("site") or {}
        if site.get("latitude") is not None and site.get("longitude") is not None:
            sentence += f", sited at {float(site['latitude']):.4f}, {float(site['longitude']):.4f}"
        facts["ifc"] = sentence
    return facts
