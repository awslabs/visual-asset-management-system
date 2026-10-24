# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Geometry attributes for the BLENDER render branch.

`compute_trimesh_attributes` derives the sys_geometry / sys_statistics / sys_format / sys_visual / sys_scene
sections from a file trimesh can read (glTF, OBJ, STL, PLY); `merge_scene_facts` folds the scene facts the
Blender script writes into them, which is the only source for formats trimesh cannot read here (FBX,
Alembic, .blend, Collada without pycollada, USD); `apply_declared_units` records the unit the file's header
declares under sys_format. Every key of the promotion source contract (PROMOTION_SOURCE_KEYS) is written
under exactly that name whenever its loader produced it. trimesh is imported lazily so the handler loads
without it. A section that cannot be computed is reported as a warning string, never stored as attribute
data.
"""

import os

MATERIAL_NAME_LIMIT = 20
# glTF defines its linear unit as metres; no other format trimesh reads here carries a unit.
GLTF_EXTENSIONS = (".glb", ".gltf")
# The branch extensions trimesh reads in this image. Collada needs pycollada (not installed); FBX, Alembic,
# .blend and USD have no trimesh loader. The Blender scene facts describe every other extension, so asking
# trimesh about one is not a warning.
TRIMESH_EXTENSIONS = (".glb", ".gltf", ".obj", ".stl", ".ply")

# The promotion source contract: the keys the metadata catalogue reads from the groups this image writes
# (camelCase, plain JSON types). `units` is one of m/mm/cm/in/ft or absent; `volume` is present only for a
# closed mesh set, because an open mesh has no defined volume.
PROMOTION_SOURCE_KEYS = {
    "sys_geometry": frozenset({"boundsMin", "boundsMax", "dimensions", "extentMax", "volume", "surfaceArea", "units"}),
    "sys_statistics": frozenset({"meshCount", "vertices", "faces", "triangles", "watertight"}),
    "sys_visual": frozenset({"materialCount", "textureCount", "hasUv", "hasVertexColors"}),
    "sys_scene": frozenset({"nodeCount", "hasAnimation", "hasArmature"}),
}


def _round(value, digits=6):
    return round(float(value), digits)


def _vector(values):
    return [_round(component) for component in values]


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _geometries(loaded, trimesh):
    if isinstance(loaded, trimesh.Scene):
        return [geometry for geometry in loaded.geometry.values() if isinstance(geometry, trimesh.Trimesh)]
    if isinstance(loaded, trimesh.Trimesh):
        return [loaded]
    return []


def _geometry(loaded, geometries, extension, trimesh):
    bounds = loaded.bounds
    size = [float(bounds[1][axis] - bounds[0][axis]) for axis in range(3)]
    section = {
        "boundsMin": _vector(bounds[0]),
        "boundsMax": _vector(bounds[1]),
        "dimensions": {"width": _round(size[0]), "height": _round(size[1]), "depth": _round(size[2])},
        "extentMax": _round(max(size)),
        "centroid": _vector((bounds[0] + bounds[1]) / 2.0),
        "surfaceArea": _round(sum(float(geometry.area) for geometry in geometries)),
    }
    if all(geometry.is_watertight for geometry in geometries):
        section["volume"] = _round(sum(float(geometry.volume) for geometry in geometries))
    if extension in GLTF_EXTENSIONS:
        section["units"] = "m"
    return section


def _statistics(loaded, geometries, extension, trimesh):
    faces = int(sum(len(geometry.faces) for geometry in geometries))
    return {
        "meshCount": len(geometries),
        "vertices": int(sum(len(geometry.vertices) for geometry in geometries)),
        "faces": faces,
        # trimesh triangulates every face on load, so its face count is the triangle count.
        "triangles": faces,
        "watertight": bool(all(geometry.is_watertight for geometry in geometries)),
        "isVolume": bool(all(geometry.is_volume for geometry in geometries)),
    }


def _format(loaded, geometries, extension, trimesh):
    section = {"extension": extension, "loader": "trimesh", "loadedType": type(loaded).__name__}
    metadata = getattr(loaded, "metadata", None) or {}
    ply_raw = metadata.get("_ply_raw") if extension == ".ply" else None
    if isinstance(ply_raw, dict):
        vertex = ply_raw.get("vertex")
        properties = vertex.get("properties") if isinstance(vertex, dict) else None
        if isinstance(properties, dict):
            section["vertexProperties"] = list(properties.keys())[:MATERIAL_NAME_LIMIT]
    return section


def _visual(loaded, geometries, extension, trimesh):
    material_names = set()
    texture_count = 0
    has_vertex_colors = False
    has_uv = False
    for geometry in geometries:
        visual = getattr(geometry, "visual", None)
        if getattr(visual, "kind", None) == "vertex":
            has_vertex_colors = True
        material = getattr(visual, "material", None)
        if material is not None:
            name = getattr(material, "name", None)
            material_names.add(str(name) if name else f"material_{len(material_names)}")
            image = getattr(material, "image", None)
            if image is None:
                image = getattr(material, "baseColorTexture", None)
            if image is not None:
                texture_count += 1
        uv = getattr(visual, "uv", None)
        if uv is not None and len(uv) > 0:
            has_uv = True
    return {
        "materialCount": len(material_names),
        "materialNames": sorted(material_names)[:MATERIAL_NAME_LIMIT],
        "textureCount": texture_count,
        "hasVertexColors": has_vertex_colors,
        "hasUv": has_uv,
    }


def _scene(loaded, geometries, extension, trimesh):
    if isinstance(loaded, trimesh.Scene):
        return {
            "nodeCount": len(loaded.graph.nodes),
            "geometryNodeCount": len(list(loaded.graph.nodes_geometry)),
            "geometryNames": sorted(loaded.geometry.keys())[:MATERIAL_NAME_LIMIT],
        }
    return {"nodeCount": 1, "geometryNodeCount": 1, "geometryNames": []}


_SECTION_BUILDERS = (
    ("sys_geometry", _geometry),
    ("sys_statistics", _statistics),
    ("sys_format", _format),
    ("sys_visual", _visual),
    ("sys_scene", _scene),
)


def compute_trimesh_attributes(path, extension):
    """(attributes, warnings): the sys_* sections trimesh derives from `path`, or ({}, [reason]) when it
    cannot load the file; ({}, []) for an extension trimesh does not read here. Never raises for a bad file."""
    if (extension or "").lower() not in TRIMESH_EXTENSIONS:
        return {}, []
    warnings = []
    try:
        import trimesh
    except ImportError as error:
        return {}, [f"trimesh unavailable: {error}"]
    name = os.path.basename(path)
    try:
        loaded = trimesh.load(path, force=None)
    except Exception as error:  # trimesh loaders raise a wide range of exceptions on unreadable input
        return {}, [f"trimesh could not load {name}: {type(error).__name__}: {error}"]
    geometries = _geometries(loaded, trimesh)
    if not geometries or getattr(loaded, "bounds", None) is None:
        return {}, [f"trimesh loaded no mesh geometry from {name}"]
    attributes = {}
    for section, builder in _SECTION_BUILDERS:
        try:
            attributes[section] = builder(loaded, geometries, extension, trimesh)
        except Exception as error:
            warnings.append(f"{section} (trimesh) failed: {type(error).__name__}: {error}")
    return attributes, warnings


def merge_scene_facts(attributes, facts, extension):
    """A new attributes dict with every section trimesh did not produce filled from Blender's scene facts,
    and the Blender-only scene keys added to sys_scene. Facts carrying `error` (an import failure) or no
    `meshObjects` contribute nothing. `units` travels with the numbers it labels: it is copied only into a
    sys_geometry built from the facts, never onto one trimesh measured."""
    merged = {key: dict(value) for key, value in (attributes or {}).items()}
    if not facts or "error" in facts or "meshObjects" not in facts:
        return merged
    bounds_min, bounds_max = facts.get("boundsMin"), facts.get("boundsMax")
    if "sys_geometry" not in merged and bounds_min and bounds_max:
        size = [float(bounds_max[axis]) - float(bounds_min[axis]) for axis in range(3)]
        geometry = {
            "boundsMin": _vector(bounds_min),
            "boundsMax": _vector(bounds_max),
            "dimensions": {"width": _round(size[0]), "height": _round(size[1]), "depth": _round(size[2])},
            "extentMax": _round(max(size)),
            "centroid": [_round((float(bounds_min[axis]) + float(bounds_max[axis])) / 2.0) for axis in range(3)],
            "upAxis": facts.get("upAxis", "Z"),
        }
        if _is_number(facts.get("surfaceArea")):
            geometry["surfaceArea"] = _round(facts["surfaceArea"])
        if _is_number(facts.get("volume")):
            geometry["volume"] = _round(facts["volume"])
        if facts.get("units"):
            geometry["units"] = str(facts["units"])
        merged["sys_geometry"] = geometry
    if "sys_statistics" not in merged:
        statistics = {
            "meshCount": int(facts["meshObjects"]),
            "vertices": int(facts.get("vertices", 0)),
            "faces": int(facts.get("faces", 0)),
            "triangles": int(facts.get("triangles", 0)),
        }
        if isinstance(facts.get("watertight"), bool):
            statistics["watertight"] = facts["watertight"]
        merged["sys_statistics"] = statistics
    if "sys_format" not in merged:
        merged["sys_format"] = {"extension": extension, "loader": "blender"}
    if "sys_visual" not in merged:
        visual = {
            "materialCount": int(facts.get("materials", 0)),
            "materialNames": list(facts.get("materialNames") or [])[:MATERIAL_NAME_LIMIT],
            "textureCount": int(facts.get("images", 0)),
        }
        for key in ("hasUv", "hasVertexColors"):
            if isinstance(facts.get(key), bool):
                visual[key] = facts[key]
        merged["sys_visual"] = visual
    scene = merged.setdefault("sys_scene", {})
    counts = dict(facts.get("objectCounts") or {})
    scene.setdefault("nodeCount", int(sum(counts.values())))
    scene.update({
        "objectCounts": counts,
        "meshObjects": int(facts["meshObjects"]),
        "hasArmature": bool(facts.get("hasArmature")),
        "hasAnimation": bool(facts.get("hasAnimation")),
        "blenderVersion": facts.get("blenderVersion"),
        "upAxis": facts.get("upAxis", "Z"),
    })
    if _is_number(facts.get("metersPerUnit")):
        scene["metersPerUnit"] = float(facts["metersPerUnit"])
        scene["metersPerUnitAuthored"] = bool(facts.get("metersPerUnitAuthored"))
    return merged


def apply_declared_units(attributes, declared, extension):
    """A new attributes dict with the unit the file's header declares (`formatUnits.declared_units`)
    recorded under sys_format as declaredMetersPerUnit / declaredUnits / declaredUnitsSource. It never
    writes sys_geometry.units: that label belongs to the loader that measured the numbers."""
    merged = {key: dict(value) for key, value in (attributes or {}).items()}
    if not declared or not _is_number(declared.get("metersPerUnit")):
        return merged
    section = merged.setdefault("sys_format", {"extension": extension})
    section["declaredMetersPerUnit"] = float(declared["metersPerUnit"])
    section["declaredUnitsSource"] = declared.get("source")
    if declared.get("units"):
        section["declaredUnits"] = declared["units"]
    return merged
