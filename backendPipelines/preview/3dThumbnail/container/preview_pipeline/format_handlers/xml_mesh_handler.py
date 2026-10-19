# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
XML-based mesh formats: 3MF (``.3mf``, a zip holding a 3D model part) and COLLADA (``.dae``).

Every input is untrusted, and both formats are XML. They are parsed with the standard library's expat
parser, hardened the way ``defusedxml`` hardens it (ARCC BSC12, secure XML parsing): a document type
declaration, an entity declaration or an external entity reference aborts the parse, and parameter
entities are never read. Neither format needs a DTD, so no legitimate file is refused, and no entity
expansion ("billion laughs") or external fetch (XXE: local files, URLs) can happen. The tree is built
with ``ElementTree.TreeBuilder``; ``ElementTree.parse`` is never called on input.

A 3MF archive is checked before it is read (``utils/archive.py``): its uncompressed size from the zip
central directory must fit the free disk, the model part must stay under ``MAX_XML_BYTES``, and no
member may have an implausible compression ratio.

A 3MF component or a COLLADA ``instance_node`` may reference the same child many times from every
level, so both walks count their nodes and placed geometry against a ``scene_limits.SceneLimit`` and
stop once it is reached; each object or geometry is parsed once, however often it is placed.

Both loaders return a Y-up PolyData with per-cell ``RGB`` when the file sets colours: 3MF is Z-up by
specification; a COLLADA file states its axis in ``<asset><up_axis>``.
"""

import posixpath
import zipfile
import xml.etree.ElementTree as ElementTree  # nosec B405 - TreeBuilder/Element only; parsing is _safe_expat
from xml.parsers import expat  # nosec B407 - hardened in _safe_expat

import numpy as np
import pyvista as pv

from ..utils.logging import get_logger
from .scene_limits import SceneLimit

logger = get_logger()

SUPPORTED_EXTENSIONS = {".3mf", ".dae"}


def can_handle(extension: str) -> bool:
    return extension.lower() in SUPPORTED_EXTENSIONS

# A model part larger than this, uncompressed, is refused (parsing holds it as a tree in memory).
MAX_XML_BYTES = 2 * 1024 ** 3
DEFAULT_RGB = (173, 216, 230)


class UnsafeXmlError(ValueError):
    pass


# --------------------------------------------------------------------------------------------------
# Hardened parsing
# --------------------------------------------------------------------------------------------------

def _forbid(kind):
    def handler(*_args):
        raise UnsafeXmlError(f"XML {kind} is not allowed in a 3D model file")
    return handler


def _qualified(name: str) -> str:
    """expat's ``uri}local`` (namespace_separator "}") as ElementTree's ``{uri}local``."""
    return "{" + name if "}" in name else name


def _safe_expat(builder: ElementTree.TreeBuilder):
    """An expat parser feeding ``builder`` that refuses DTDs, entity declarations and external
    entities and never reads parameter entities."""
    parser = expat.ParserCreate(namespace_separator="}")  # nosec B407 - handlers below refuse DTDs/entities
    parser.StartDoctypeDeclHandler = _forbid("document type declaration")
    parser.EntityDeclHandler = _forbid("entity declaration")
    parser.UnparsedEntityDeclHandler = _forbid("entity declaration")
    parser.ExternalEntityRefHandler = _forbid("external entity reference")
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.StartElementHandler = lambda name, attributes: builder.start(
        _qualified(name), {_qualified(key): value for key, value in attributes.items()})
    parser.EndElementHandler = lambda name: builder.end(_qualified(name))
    parser.CharacterDataHandler = builder.data
    return parser


def parse_xml(source, chunk_bytes: int = 4 * 1024 ** 2):
    """The root element of an XML file path or binary file object, parsed by ``_safe_expat``."""
    builder = ElementTree.TreeBuilder()
    parser = _safe_expat(builder)
    handle = open(source, "rb") if isinstance(source, str) else source
    try:
        while True:
            chunk = handle.read(chunk_bytes)
            if not chunk:
                break
            parser.Parse(chunk, False)
        parser.Parse(b"", True)
    except expat.ExpatError as e:
        raise ValueError(f"Invalid XML: {e}")
    finally:
        if isinstance(source, str):
            handle.close()
    return builder.close()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(element, name):
    return [child for child in element if _local(child.tag) == name]


def _child(element, name):
    return next((child for child in element if _local(child.tag) == name), None)


def _numbers(text, dtype=np.float64) -> np.ndarray:
    return np.array((text or "").split(), dtype=dtype)


def _hex_colour(value):
    value = (value or "").strip().lstrip("#")
    if len(value) in (6, 8):
        try:
            return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return None
    return None


def _z_up_to_y_up(points: np.ndarray) -> np.ndarray:
    return np.column_stack([points[:, 0], points[:, 2], -points[:, 1]])


def _polydata(points, triangles, colours) -> pv.PolyData:
    if not len(triangles):
        raise ValueError("No triangles found")
    pv_faces = np.column_stack([np.full(len(triangles), 3, dtype=np.int64), triangles]).ravel()
    mesh = pv.PolyData(np.ascontiguousarray(points, dtype=np.float64), pv_faces)
    if colours is not None:
        mesh.cell_data["RGB"] = np.ascontiguousarray(colours, dtype=np.uint8)
    return mesh


# --------------------------------------------------------------------------------------------------
# 3MF
# --------------------------------------------------------------------------------------------------

_MODEL_REL = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"


def _model_part(archive: zipfile.ZipFile) -> str:
    names = set(archive.namelist())
    if "_rels/.rels" in names:
        rels = parse_xml(archive.open("_rels/.rels"))
        for rel in rels:
            if rel.get("Type") == _MODEL_REL and rel.get("Target"):
                target = posixpath.normpath(rel.get("Target").lstrip("/"))
                if target in names:
                    return target
    if "3D/3dmodel.model" in names:
        return "3D/3dmodel.model"
    raise ValueError("3MF archive has no 3D model part")


def _transform(text) -> np.ndarray:
    """A 3MF 3x4 transform (row-vector convention) as a 4x4 matrix applied as ``p @ M[:3,:3] + M[3,:3]``."""
    values = _numbers(text)
    matrix = np.eye(4)
    if len(values) == 12:
        matrix[:4, :3] = values.reshape(4, 3)
    return matrix


def load_3mf(file_path: str, max_triangles: int = None) -> pv.PolyData:
    """A 3MF build as one Y-up mesh. The component walk stops at a ``SceneLimit`` of
    ``max_triangles``; each object's own mesh is parsed once however often it is placed."""
    import os

    from ..utils.archive import check_archive, check_member

    check_archive(file_path, os.path.dirname(os.path.abspath(file_path)))
    with zipfile.ZipFile(file_path) as archive:
        part = _model_part(archive)
        check_member(archive.getinfo(part), MAX_XML_BYTES)
        model = parse_xml(archive.open(part))
    resources = _child(model, "resources")
    if resources is None:
        raise ValueError("3MF model has no resources")

    materials = {}
    for group in _children(resources, "basematerials"):
        materials[group.get("id")] = [_hex_colour(base.get("displaycolor")) for base in _children(group, "base")]
    for group in _children(resources, "colorgroup"):
        materials[group.get("id")] = [_hex_colour(colour.get("color")) for colour in _children(group, "color")]

    objects = {obj.get("id"): obj for obj in _children(resources, "object")}
    coloured = bool(materials)
    limit = SceneLimit("3MF build", max_triangles)
    meshes = {}
    points_out, cells_out, colours_out = [], [], []
    offset = [0]

    def colour_for(pid, index):
        palette = materials.get(pid)
        if palette and index is not None and 0 <= index < len(palette) and palette[index]:
            return palette[index]
        return None

    def object_mesh(obj, mesh):
        """(vertices, triangles, colours or None) of the object's ``<mesh>``, or None when unusable."""
        vertices = _child(mesh, "vertices")
        triangles = _child(mesh, "triangles")
        if vertices is None or triangles is None:
            return None
        xyz = np.array([[float(v.get("x", 0)), float(v.get("y", 0)), float(v.get("z", 0))]
                        for v in _children(vertices, "vertex")], dtype=np.float64)
        rows = _children(triangles, "triangle")
        tri = np.array([[int(t.get("v1")), int(t.get("v2")), int(t.get("v3"))] for t in rows], dtype=np.int64)
        if not len(xyz) or not len(tri) or tri.max() >= len(xyz) or tri.min() < 0:
            return None
        colours = None
        if coloured:
            default_pid, default_index = obj.get("pid"), obj.get("pindex")
            default = colour_for(default_pid, int(default_index) if default_index else 0)
            colours = np.array([colour_for(t.get("pid", default_pid), int(t.get("p1")) if t.get("p1") else None)
                                or default or DEFAULT_RGB for t in rows], dtype=np.uint8)
        return xyz, tri, colours

    def emit(obj, matrix, depth):
        if depth > 32:
            raise ValueError("3MF component nesting deeper than 32 levels")
        if not limit.visit():
            return
        mesh = _child(obj, "mesh")
        if mesh is not None:
            key = obj.get("id")
            if key not in meshes:
                meshes[key] = object_mesh(obj, mesh)
            if meshes[key] is None:
                return
            xyz, tri, colours = meshes[key]
            points_out.append(xyz @ matrix[:3, :3] + matrix[3, :3])
            cells_out.append(tri + offset[0])
            offset[0] += len(xyz)
            if coloured:
                colours_out.append(colours)
            limit.add(len(xyz), len(tri))
        components = _child(obj, "components")
        if components is not None:
            for component in _children(components, "component"):
                if limit.reached:
                    return
                child = objects.get(component.get("objectid"))
                if child is not None:
                    emit(child, _transform(component.get("transform")) @ matrix, depth + 1)

    build = _child(model, "build")
    items = _children(build, "item") if build is not None else []
    for item in items:
        if limit.reached:
            break
        obj = objects.get(item.get("objectid"))
        if obj is not None:
            emit(obj, _transform(item.get("transform")), 0)
    if not points_out:
        raise ValueError("3MF build holds no mesh")
    points = _z_up_to_y_up(np.vstack(points_out))
    mesh = _polydata(points, np.vstack(cells_out), np.vstack(colours_out) if coloured else None)
    logger.info(f"Loaded 3MF: {mesh.n_points} vertices, {mesh.n_cells} triangles from {len(items)} build item(s)")
    return mesh


# --------------------------------------------------------------------------------------------------
# COLLADA
# --------------------------------------------------------------------------------------------------

def _node_matrix(node) -> np.ndarray:
    """A COLLADA node's transform elements composed in order, as a column-vector 4x4 matrix."""
    matrix = np.eye(4)
    for element in node:
        name = _local(element.tag)
        values = _numbers(element.text)
        if name == "matrix" and len(values) == 16:
            step = values.reshape(4, 4)
        elif name == "translate" and len(values) == 3:
            step = np.eye(4)
            step[:3, 3] = values
        elif name == "scale" and len(values) == 3:
            step = np.diag([values[0], values[1], values[2], 1.0])
        elif name == "rotate" and len(values) == 4:
            axis = values[:3] / (np.linalg.norm(values[:3]) or 1.0)
            angle = np.radians(values[3])
            x, y, z = axis
            c, s, t = np.cos(angle), np.sin(angle), 1 - np.cos(angle)
            step = np.eye(4)
            step[:3, :3] = [[t * x * x + c, t * x * y - s * z, t * x * z + s * y],
                            [t * x * y + s * z, t * y * y + c, t * y * z - s * x],
                            [t * x * z - s * y, t * y * z + s * x, t * z * z + c]]
        else:
            continue
        matrix = matrix @ step
    return matrix


def _material_colours(root) -> dict:
    """material id -> RGB from its effect's diffuse colour."""
    effects = {}
    library = _child(root, "library_effects")
    for effect in (_children(library, "effect") if library is not None else []):
        for element in effect.iter():
            if _local(element.tag) == "diffuse":
                colour = _child(element, "color")
                if colour is not None:
                    values = _numbers(colour.text)
                    if len(values) >= 3:
                        effects[effect.get("id")] = tuple(int(round(min(max(v, 0), 1) * 255)) for v in values[:3])
                break
    materials = {}
    library = _child(root, "library_materials")
    for material in (_children(library, "material") if library is not None else []):
        instance = _child(material, "instance_effect")
        if instance is not None:
            colour = effects.get((instance.get("url") or "").lstrip("#"))
            if colour:
                materials[material.get("id")] = colour
    return materials


def _geometry_triangles(geometry):
    """[(points, triangles, material symbol)] for a COLLADA geometry's triangle/polygon primitives."""
    mesh = _child(geometry, "mesh")
    if mesh is None:
        return []
    sources = {}
    for source in _children(mesh, "source"):
        array = _child(source, "float_array")
        technique = _child(source, "technique_common")
        accessor = _child(technique, "accessor") if technique is not None else None
        stride = int(accessor.get("stride", 3)) if accessor is not None else 3
        if array is not None:
            values = _numbers(array.text)
            sources[source.get("id")] = values[: len(values) // stride * stride].reshape(-1, stride)
    vertices = _child(mesh, "vertices")
    position_source = None
    if vertices is not None:
        for entry in _children(vertices, "input"):
            if entry.get("semantic") == "POSITION":
                position_source = (entry.get("source") or "").lstrip("#")
    out = []
    for primitive in mesh:
        kind = _local(primitive.tag)
        if kind not in ("triangles", "polylist", "polygons"):
            continue
        inputs = _children(primitive, "input")
        n_offsets = max([int(entry.get("offset", 0)) for entry in inputs] + [0]) + 1
        vertex_offset = next((int(entry.get("offset", 0)) for entry in inputs if entry.get("semantic") == "VERTEX"), None)
        source_id = position_source
        if vertex_offset is None:
            entry = next((entry for entry in inputs if entry.get("semantic") == "POSITION"), None)
            if entry is None:
                continue
            vertex_offset, source_id = int(entry.get("offset", 0)), (entry.get("source") or "").lstrip("#")
        positions = sources.get(source_id)
        if positions is None or positions.shape[1] < 3:
            continue
        p_elements = _children(primitive, "p")
        if kind == "polygons":
            polygons = [_numbers(p.text, np.int64).reshape(-1, n_offsets)[:, vertex_offset] for p in p_elements]
        else:
            indices = np.concatenate([_numbers(p.text, np.int64) for p in p_elements]) if p_elements else np.array([], np.int64)
            corners = indices[: len(indices) // n_offsets * n_offsets].reshape(-1, n_offsets)[:, vertex_offset]
            if kind == "triangles":
                polygons = None
                triangles = corners[: len(corners) // 3 * 3].reshape(-1, 3)
            else:
                counts = _numbers(_child(primitive, "vcount").text, np.int64) if _child(primitive, "vcount") is not None else np.array([], np.int64)
                starts = np.concatenate([[0], np.cumsum(counts)[:-1]]) if len(counts) else np.array([], np.int64)
                polygons = [corners[s:s + c] for s, c in zip(starts, counts) if s + c <= len(corners)]
        if kind != "triangles":
            fans = [np.column_stack([np.full(len(poly) - 2, poly[0]), poly[1:-1], poly[2:]])
                    for poly in polygons if len(poly) >= 3]
            triangles = np.vstack(fans) if fans else np.empty((0, 3), np.int64)
        triangles = triangles[(triangles >= 0).all(axis=1) & (triangles < len(positions)).all(axis=1)]
        if len(triangles):
            out.append((positions[:, :3], triangles, primitive.get("material")))
    return out


def load_dae(file_path: str, max_triangles: int = None) -> pv.PolyData:
    """A COLLADA scene as one Y-up mesh. The node walk stops at a ``SceneLimit`` of
    ``max_triangles``; each geometry is parsed once however often it is instanced."""
    import os

    size = os.path.getsize(file_path)
    if size > MAX_XML_BYTES:
        raise ValueError(f"COLLADA file is {size / 1024 ** 3:.2f} GiB, over the {MAX_XML_BYTES / 1024 ** 3:.0f} GiB limit")
    root = parse_xml(file_path)
    materials = _material_colours(root)
    library = _child(root, "library_geometries")
    geometries = {g.get("id"): g for g in (_children(library, "geometry") if library is not None else [])}
    nodes_library = _child(root, "library_nodes")
    library_nodes = {n.get("id"): n for n in (nodes_library.iter() if nodes_library is not None else [])
                     if _local(n.tag) == "node" and n.get("id")}
    coloured = bool(materials)
    limit = SceneLimit("COLLADA scene", max_triangles)
    primitives = {}
    points_out, cells_out, colours_out = [], [], []
    offset = [0]

    def add_geometry(instance, matrix):
        geometry_id = (instance.get("url") or "").lstrip("#")
        geometry = geometries.get(geometry_id)
        if geometry is None or limit.reached:
            return
        if geometry_id not in primitives:
            primitives[geometry_id] = _geometry_triangles(geometry)
        bindings = {}
        for bound in instance.iter():
            if _local(bound.tag) == "instance_material":
                bindings[bound.get("symbol")] = (bound.get("target") or "").lstrip("#")
        for positions, triangles, symbol in primitives[geometry_id]:
            if limit.reached:
                return
            homogeneous = np.column_stack([positions, np.ones(len(positions))])
            points_out.append((homogeneous @ matrix.T)[:, :3])
            cells_out.append(triangles + offset[0])
            offset[0] += len(positions)
            if coloured:
                colour = materials.get(bindings.get(symbol, symbol)) or DEFAULT_RGB
                colours_out.append(np.tile(np.array(colour, dtype=np.uint8), (len(triangles), 1)))
            limit.add(len(positions), len(triangles))

    def walk(node, parent, depth):
        if depth > 64 or not limit.visit():
            return
        matrix = parent @ _node_matrix(node)
        for child in node:
            if limit.reached:
                return
            name = _local(child.tag)
            if name == "instance_geometry":
                add_geometry(child, matrix)
            elif name == "node":
                walk(child, matrix, depth + 1)
            elif name == "instance_node":
                target = library_nodes.get((child.get("url") or "").lstrip("#"))
                if target is not None:
                    walk(target, matrix, depth + 1)

    scenes = _child(root, "library_visual_scenes")
    scene_ref = _child(_child(root, "scene"), "instance_visual_scene") if _child(root, "scene") is not None else None
    chosen = None
    for scene in (_children(scenes, "visual_scene") if scenes is not None else []):
        if chosen is None or (scene_ref is not None and scene.get("id") == (scene_ref.get("url") or "").lstrip("#")):
            chosen = scene
    if chosen is not None:
        for node in _children(chosen, "node"):
            walk(node, np.eye(4), 0)
    if not points_out:
        # No scene: draw every geometry untransformed.
        for geometry_id in geometries:
            add_geometry(ElementTree.Element("instance_geometry", url="#" + geometry_id), np.eye(4))
    if not points_out:
        raise ValueError("COLLADA file holds no triangle geometry")
    points = np.vstack(points_out)
    asset = _child(root, "asset")
    up_axis = (_child(asset, "up_axis").text or "").strip().upper() if asset is not None and _child(asset, "up_axis") is not None else "Y_UP"
    if up_axis == "Z_UP":
        points = _z_up_to_y_up(points)
    elif up_axis == "X_UP":
        points = np.column_stack([-points[:, 1], points[:, 0], points[:, 2]])
    mesh = _polydata(points, np.vstack(cells_out), np.vstack(colours_out) if coloured else None)
    logger.info(f"Loaded COLLADA ({up_axis}): {mesh.n_points} vertices, {mesh.n_cells} triangles")
    return mesh


def load(file_path: str, ext: str, max_triangles: int = None) -> pv.PolyData:
    if ext == ".3mf":
        return load_3mf(file_path, max_triangles)
    if ext == ".dae":
        return load_dae(file_path, max_triangles)
    raise ValueError(f"Unsupported XML mesh format: {ext}")
