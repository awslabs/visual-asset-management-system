# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""OpenUSD reader and writer for the conversion (runs in the worker child only).

Reader: a ``Usd.Stage`` becomes a ``trimesh.Scene`` (meshes with their world transform baked into the
vertices, UV coordinates, the UsdPreviewSurface base colour and its texture; ``UsdGeom.Points`` as a
point cloud). Writer: a ``trimesh`` scene or mesh becomes ``UsdGeom`` meshes under one Xform per
placement, with normals, a ``st`` UV primvar, a UsdPreviewSurface material, explicit ``metersPerUnit``,
``upAxis`` and ``defaultPrim``. USDZ output is a package made with ``UsdUtils.CreateNewUsdzPackage``.

A loose USD file may name other files (sublayers, references, payloads, textures). Before the stage is
opened every such name is checked: a name that is absolute, carries a URI scheme, leaves the input's
directory or is not among the files already fetched next to the input is refused, so the USD resolver
never reads anything else. For a USDZ the check runs over the files inside the package.
"""

import os
import posixpath
import re
import shutil
import tempfile

import numpy as np

import scene_limits
import vertex_normals
from common.logger import safeLogger

logger = safeLogger(service="conversionTrimeshPipeline")

# Bounds on the dependency walk of one input.
MAX_LAYERS = 200
MAX_DEPTH = 8
# USD's own fallback when a layer authors no metersPerUnit.
USD_DEFAULT_METERS_PER_UNIT = 0.01

_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")


class UsdReferenceError(ValueError):
    """A name in a USD file points somewhere the conversion does not read."""


def reference_problem(reference):
    """Why ``reference`` (an asset path as the layer spells it) is not followed, or None."""
    text = str(reference)
    if not text or "\x00" in text:
        return "is empty or holds a control character"
    if _SCHEME.match(text) and not re.match(r"^[A-Za-z]:[\\/]", text):
        return "names a URI scheme"
    if re.match(r"^[A-Za-z]:[\\/]", text) or text.startswith(("/", "\\")):
        return "is an absolute path"
    if any(part == ".." for part in re.split(r"[\\/]", text)):
        return "leaves the asset through '..'"
    return None


def _import_pxr():
    from pxr import Ar, Gf, Sdf, Tf, Usd, UsdGeom, UsdShade, UsdUtils, Vt  # noqa: F401
    return Ar, Gf, Sdf, Tf, Usd, UsdGeom, UsdShade, UsdUtils, Vt


def confine_dependencies(path, input_dir, skipped=None):
    """Make the USD input safe to open and return the local files its layers name.

    A loose layer (``.usd``, ``.usda``, ``.usdc``) is a local copy of the asset's file, so every asset
    path it holds that is not a usable local file -- absolute, a URI scheme, ``..`` out of the input's
    directory, not among the fetched files, or past ``MAX_DEPTH`` / ``MAX_LAYERS`` -- is removed from
    it with ``UsdUtils.ModifyAssetPaths`` before the stage is opened, so the USD resolver never opens
    it. Each removal is appended to ``skipped`` as ``(name, reason, is_layer)``. A ``.usdz`` package
    cannot be edited: a layer it names outside the package that is not a relative path inside the asset
    fails the run (``UsdReferenceError``); a missing one cannot be opened, and a texture is read only
    from inside the input's directory (``_open_texture``)."""
    from pxr import Sdf, UsdUtils
    skipped = [] if skipped is None else skipped
    root = os.path.realpath(input_dir)
    if path.lower().endswith(".usdz"):
        _check_package(path, root)
        return []
    found = set()
    seen = set()
    queue = [(os.path.realpath(path), 0)]
    while queue:
        local_layer, depth = queue.pop(0)
        if local_layer in seen:
            continue
        seen.add(local_layer)
        try:
            layer = Sdf.Layer.FindOrOpen(local_layer)
        except Exception as e:  # noqa: BLE001 - an unreadable layer is the input's problem
            raise UsdReferenceError(f"The USD layer {os.path.basename(local_layer)} could not be read: {e}")
        if layer is None:
            raise UsdReferenceError(f"The USD layer {os.path.basename(local_layer)} could not be read.")
        children = []

        def keep(asset_path, layer_dir=os.path.dirname(local_layer), depth=depth):
            text = str(asset_path)
            if not text:
                return text
            is_layer = _is_layer_name(text.split("[", 1)[0])
            problem = reference_problem(text)
            # A '..' that stays inside the input's asset is a legitimate relative path here; where it
            # lands is checked against the asset's local root below.
            if problem == "leaves the asset through '..'":
                problem = None
            local = None
            if problem is None:
                local = os.path.realpath(os.path.join(layer_dir, text.split("[", 1)[0]))
                if local != root and not local.startswith(root + os.sep):
                    problem = "leaves the asset"
                elif not os.path.isfile(local):
                    problem = "is not among the files fetched with the input"
                elif is_layer and depth + 1 > MAX_DEPTH:
                    problem = f"is nested deeper than {MAX_DEPTH} levels"
                elif is_layer and local not in seen and len(seen) + len(children) >= MAX_LAYERS:
                    problem = f"is past the {MAX_LAYERS} layers one input may compose"
            if problem is not None:
                skipped.append((text[:200], problem, is_layer))
                return ""
            found.add(local)
            if is_layer:
                children.append(local)
            return text

        before = layer.ExportToString()
        UsdUtils.ModifyAssetPaths(layer, keep)
        if layer.ExportToString() != before:
            layer.Save()
        for child in children:
            queue.append((child, depth + 1))
    return sorted(found)


def _check_package(path, root):
    """Refuse a ``.usdz`` whose root layer names a layer outside the package by an absolute path, a
    URI scheme or ``..``: the package cannot be edited, and the resolver would open that path."""
    from pxr import UsdUtils
    sublayers, references, payloads = UsdUtils.ExtractExternalReferences(path)
    for reference in list(sublayers) + list(references) + list(payloads):
        problem = reference_problem(reference)
        if problem and _is_layer_name(str(reference).split("[", 1)[0]):
            raise UsdReferenceError(f"The USD package names {str(reference)[:200]!r}, which {problem}.")


def dependency_graph(path, input_dir):
    """``{local layer: [local files it names]}`` of a confined loose USD input (call
    ``confine_dependencies`` first), every name resolved against its layer's directory.

    Only what loads something is an edge: composition arcs (sublayers, references, payloads, variants
    included), value clips, and the values of ``asset``-typed attributes (textures). Layer and prim
    metadata are not -- above all a component asset's ``assetInfo`` identifier, which names the layer
    itself -- the same rule ``reference_collectors._usd_reference_kinds`` applies when the files are
    fetched. A layer naming itself through a composition arc is not an edge either; USD does not
    compose a layer into itself."""
    import reference_collectors
    graph = {}
    queue = [os.path.realpath(path)]
    while queue:
        local_layer = queue.pop(0)
        if local_layer in graph:
            continue
        kinds = reference_collectors._usd_reference_kinds(local_layer)
        if kinds is None:
            raise ValueError(f"The USD layer {os.path.basename(local_layer)} could not be read for its references.")
        targets = []
        for reference in kinds["composition"] | kinds["clips"] | kinds["attributes"]:
            text = str(reference).split("[", 1)[0]
            if not text:
                continue
            local = os.path.realpath(os.path.join(os.path.dirname(local_layer), text))
            if local != local_layer and os.path.isfile(local):
                targets.append(local)
        graph[local_layer] = sorted(set(targets))
        queue.extend(t for t in targets if _is_layer_name(t) and t not in graph)
    return graph


def _leaves_first(graph, start):
    """The layers of ``graph`` reachable from ``start``, each after the layers it names. A cycle is
    refused, naming the layers on it."""
    order, state = [], {}

    def visit(node, trail):
        if state.get(node) == "done":
            return
        if state.get(node) == "open":
            cycle = trail[trail.index(node):] + [node]
            raise ValueError("REFERENCE_HANDLING convert-linked cannot convert a reference cycle: "
                             + " -> ".join(os.path.basename(n) for n in cycle))
        state[node] = "open"
        for child in graph.get(node, []):
            if _is_layer_name(child):
                visit(child, trail + [node])
        state[node] = "done"
        order.append(node)

    visit(start, [])
    return order


# The folder a converted layer that sits outside the selected file's folder is written to, at its path
# from the asset root, so every converted file stays under the run's output.
LINKED_OUTSIDE_FOLDER = "_linked"


def convert_linked(path, input_dir, output_file, output_extension, output_path_extension=None):
    """REFERENCE_HANDLING convert-linked: convert the selected USD file AND every USD layer it names,
    leaves first, each to ``output_extension`` (``.usdc`` or ``.usda``), keeping the sub-folders,
    rewriting each layer's references to the converted files' names relative to it, and copying the
    other files (textures, ``.usdz`` packages) beside them. Nothing is composed or flattened; each
    layer keeps its own metersPerUnit and upAxis. ``output_path_extension`` is the run's output path
    prefix: references are written to where the write-back puts each file, in the same pass, so no
    layer is opened a second time. Returns the output paths written."""
    from pxr import Sdf, UsdUtils
    root = os.path.realpath(input_dir)
    source = os.path.realpath(path)
    source_dir = os.path.dirname(source)
    out_dir = os.path.dirname(os.path.abspath(output_file))
    graph = dependency_graph(source, root)
    layers = _leaves_first(graph, source)
    files = sorted({t for targets in graph.values() for t in targets if not _is_layer_name(t)})

    def destination(local):
        if local == source:
            return os.path.abspath(output_file)
        relative = os.path.relpath(local, source_dir)
        if relative.startswith(".."):
            relative = os.path.join(LINKED_OUTSIDE_FOLDER, os.path.relpath(local, root))
        if _is_layer_name(local):
            relative = os.path.splitext(relative)[0] + output_extension
        return os.path.join(out_dir, relative)

    mapping = {local: destination(local) for local in layers + files}
    clashes = {}
    for local, target in mapping.items():
        clashes.setdefault(target, []).append(local)
    for target, sources in clashes.items():
        if len(sources) > 1:
            raise ValueError("REFERENCE_HANDLING convert-linked would write "
                             + " and ".join(os.path.relpath(s, root) for s in sources)
                             + f" to the same file, {os.path.relpath(target, out_dir)}. Rename one of them.")
    for local in files:
        os.makedirs(os.path.dirname(mapping[local]), exist_ok=True)
        shutil.copyfile(local, mapping[local])
    for local in layers:
        target = mapping[local]
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if not Sdf.Layer.FindOrOpen(local).Export(target):
            raise RuntimeError(f"{os.path.basename(local)} could not be written as {output_extension}.")
        written = Sdf.Layer.FindOrOpen(target)
        written.Reload(force=True)

        def relink(asset_path, layer_dir=os.path.dirname(local), target=target):
            text = str(asset_path)
            base, _, member = text.partition("[")
            if not base:
                return text
            resolved = os.path.realpath(os.path.join(layer_dir, base))
            if resolved not in mapping:
                return text
            relinked = placed_reference(os.path.relpath(target, out_dir).replace(os.sep, "/"),
                                        os.path.relpath(mapping[resolved], out_dir).replace(os.sep, "/"),
                                        output_path_extension)
            return relinked + ("[" + member if member else "")

        UsdUtils.ModifyAssetPaths(written, relink)
        written.Save()
    return [mapping[local] for local in layers] + [mapping[f] for f in files]


# Vendored from backend/backend/common/workflows/outputPathExtension.py (tests/test_3dbasic_output_prefix.py
# keeps both functions identical): how the workflow's write-back places an execution's output path
# extension in each output file's asset-relative path.
NO_EXTENSION = "/"


def normalize_output_path_extension(raw):
    """Canonicalize an authored extension to a single leading `/` plus its authored trailing `/`.

    Returns `NO_EXTENSION` for None/empty/`"/"`. Any `{{tag}}` placeholders pass through untouched —
    they are substituted once the launch has a manifest, and the result is normalized again. Traversal
    and backslashes are rejected by the request model, not here.
    """
    if raw is None:
        return NO_EXTENSION
    text = str(raw).strip()
    if text in ("", "/"):
        return NO_EXTENSION
    is_folder = text.endswith("/")
    segments = [segment for segment in text.split("/") if segment]
    if not segments:
        return NO_EXTENSION
    body = "/".join(segments)
    return f"/{body}/" if is_folder else f"/{body}"


def apply_output_path_extension(relative_path, extension):
    """Insert `extension` into `relative_path` immediately before the final filename.

    `relative_path` is asset-relative (a leading `/` is tolerated and dropped, matching the
    bucket-relative keys the output lambdas build). Returns the path unchanged when there is no
    extension, and returns an empty/only-slash path unchanged (there is no filename to place before).
    """
    relative = (relative_path or "").lstrip("/")
    normalized = normalize_output_path_extension(extension)
    if not relative or normalized == NO_EXTENSION:
        return relative
    insert = normalized.lstrip("/")
    head, separator, filename = relative.rpartition("/")
    return f"{head}{separator}{insert}{filename}"


def package_usdz(path, input_dir, output_file):
    """REFERENCE_HANDLING package: keep the USD input's layers and composition as authored and pack
    the layer and everything it names (the confined dependency closure, textures included) into one
    ``.usdz`` with ``UsdUtils.CreateNewUsdzPackage``."""
    from pxr import Sdf, UsdUtils
    if not UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(os.path.realpath(path)), output_file):
        raise RuntimeError("The USDZ package could not be written.")


def _package_member(identifier):
    """The member part of ``pkg.usdz[member]``, or '' for the package itself."""
    text = str(identifier)
    if text.endswith("]") and "[" in text:
        return text[text.rindex("[") + 1:-1]
    return ""


def _is_layer_name(name):
    return str(name).lower().endswith((".usd", ".usda", ".usdc"))


def read_usd(path, input_dir, limit=None):
    """``(scene, meters_per_unit, up_axis)`` of a USD file. ``up_axis`` is 'y' or 'z'."""
    import trimesh
    Ar, Gf, Sdf, Tf, Usd, UsdGeom, UsdShade, UsdUtils, Vt = _import_pxr()
    skipped = []
    confine_dependencies(path, input_dir, skipped)
    if skipped:
        logger.warning(f"{len(skipped)} file(s) named by the USD file were left out: "
                       + "; ".join(f"{name[:100]} ({reason})" for name, reason, _ in skipped[:5]))
    allowed_root = os.path.realpath(input_dir)
    try:
        stage = Usd.Stage.Open(path)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"{os.path.basename(path)} could not be opened as USD: {e}")
    if stage is None:
        raise ValueError(f"{os.path.basename(path)} could not be opened as USD")
    meters = UsdGeom.GetStageMetersPerUnit(stage) or USD_DEFAULT_METERS_PER_UNIT
    up = str(UsdGeom.GetStageUpAxis(stage)).lower()
    up = 'z' if up == 'z' else 'y'
    limit = limit or scene_limits.SceneLimit(os.path.basename(path))
    scene = trimesh.Scene()
    cache = UsdGeom.XformCache()
    textures = {}
    count = 0
    for prim in Usd.PrimRange(stage.GetPseudoRoot(), Usd.TraverseInstanceProxies()):
        if not limit.visit():
            break
        imageable = UsdGeom.Imageable(prim)
        if not imageable or not prim.IsActive():
            continue
        if imageable.ComputeVisibility() == UsdGeom.Tokens.invisible:
            continue
        if imageable.ComputePurpose() not in (UsdGeom.Tokens.default_, UsdGeom.Tokens.render):
            continue
        world = np.array(cache.GetLocalToWorldTransform(prim), dtype=np.float64).T
        if prim.IsA(UsdGeom.Mesh):
            geometry = _read_mesh(UsdGeom.Mesh(prim), world, textures, trimesh, allowed_root)
        elif prim.IsA(UsdGeom.Points):
            geometry = _read_points(UsdGeom.Points(prim), world, trimesh)
        elif _implicit_type(prim, UsdGeom) is not None:
            geometry = _read_implicit(prim, world, textures, trimesh, allowed_root, UsdGeom)
        else:
            continue
        if geometry is None:
            continue
        faces = len(geometry.faces) if hasattr(geometry, "faces") else 0
        limit.add(len(geometry.vertices), faces)
        if limit.reached:
            raise ValueError(
                f"{os.path.basename(path)} expands past the size this function converts "
                f"({limit.what}); reduce the scene's instancing or size.")
        count += 1
        scene.add_geometry(geometry, node_name=str(prim.GetPath()).strip("/").replace("/", "_") or "geometry",
                           geom_name=str(prim.GetPath()).strip("/").replace("/", "_") or "geometry")
    if count == 0:
        layers = [name for name, _, is_layer in skipped if is_layer]
        missing = (f" The geometry layers it names could not be used: {', '.join(layers[:20])}."
                   if layers else "")
        raise ValueError(f"{os.path.basename(path)} contains no geometry, so there is nothing to convert.{missing}")
    return scene, float(meters), up


# Segments around the axis of a tessellated Cylinder, Cone or Capsule and the subdivision of a
# tessellated Sphere: smooth enough to look round, small next to real meshes.
IMPLICIT_SECTIONS = 48
IMPLICIT_SPHERE_SUBDIVISIONS = 3


def _implicit_type(prim, UsdGeom):
    for name in ("Cube", "Sphere", "Cylinder", "Cone", "Capsule", "Plane"):
        schema = getattr(UsdGeom, name, None)
        if schema is not None and prim.IsA(schema):
            return name
    return None


def _axis_rotation(axis):
    """The 4x4 rotation taking trimesh's Z-aligned primitive to the USD ``axis`` token."""
    axis = str(axis).upper()
    if axis == "X":
        return np.array([[0, 0, 1, 0], [0, 1, 0, 0], [-1, 0, 0, 0], [0, 0, 0, 1]], dtype=np.float64)
    if axis == "Y":
        return np.array([[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]], dtype=np.float64)
    return np.eye(4)


def _read_implicit(prim, world, textures, trimesh, allowed_root, UsdGeom):
    """A UsdGeom Cube, Sphere, Cylinder, Cone, Capsule or Plane tessellated to a mesh with its
    authored size (or the schema's fallback), placed by its world transform."""
    kind = _implicit_type(prim, UsdGeom)
    schema = getattr(UsdGeom, kind)(prim)

    def value(name, default):
        attribute = getattr(schema, f"Get{name}Attr", None)
        got = attribute().Get() if attribute is not None else None
        return default if got is None else got

    if kind == "Cube":
        mesh = trimesh.creation.box(extents=[float(value("Size", 2.0))] * 3)
    elif kind == "Sphere":
        mesh = trimesh.creation.icosphere(subdivisions=IMPLICIT_SPHERE_SUBDIVISIONS,
                                          radius=float(value("Radius", 1.0)))
    else:
        axis = value("Axis", "Z")
        if kind == "Plane":
            width, length = float(value("Width", 2.0)), float(value("Length", 2.0))
            # UsdGeomPlane's axis is its normal. For Z it lies in XY, width along X and length along Y;
            # the rotation below gives X (width along Z, length along Y) and Y (width along X, length
            # along Z), as the schema defines them.
            vertices = np.array([[-width / 2, -length / 2, 0], [width / 2, -length / 2, 0],
                                 [width / 2, length / 2, 0], [-width / 2, length / 2, 0]])
            mesh = trimesh.Trimesh(vertices=vertices, faces=[[0, 1, 2], [0, 2, 3]], process=False)
        else:
            height, radius = float(value("Height", 2.0)), float(value("Radius", 1.0))
            if kind == "Cylinder":
                mesh = trimesh.creation.cylinder(radius=radius, height=height, sections=IMPLICIT_SECTIONS)
            elif kind == "Cone":
                mesh = trimesh.creation.cone(radius=radius, height=height, sections=IMPLICIT_SECTIONS)
                mesh.apply_translation([0, 0, -height / 2])
            else:
                mesh = trimesh.creation.capsule(height=height, radius=radius, count=[IMPLICIT_SECTIONS, IMPLICIT_SECTIONS // 2])
        mesh.apply_transform(_axis_rotation(axis))
    mesh.apply_transform(world)
    color, _ = _read_material(prim, textures, allowed_root)
    if color is None:
        display = UsdGeom.Gprim(prim).GetDisplayColorAttr().Get()
        if display is not None and len(display):
            color = [int(round(max(0.0, min(1.0, float(c))) * 255)) for c in display[0]] + [255]
    if color is not None:
        mesh.visual = trimesh.visual.TextureVisuals(
            material=trimesh.visual.material.PBRMaterial(baseColorFactor=color))
    return mesh


def _read_points(points_prim, world, trimesh):
    values = points_prim.GetPointsAttr().Get()
    if values is None or len(values) == 0:
        return None
    array = np.asarray(values, dtype=np.float64).reshape(-1, 3)
    array = (np.c_[array, np.ones(len(array))] @ world.T)[:, :3]
    return trimesh.PointCloud(array)


def _read_mesh(mesh_prim, world, textures, trimesh, allowed_root=None):
    points = mesh_prim.GetPointsAttr().Get()
    counts = mesh_prim.GetFaceVertexCountsAttr().Get()
    indices = mesh_prim.GetFaceVertexIndicesAttr().Get()
    if points is None or counts is None or indices is None or len(points) == 0 or len(counts) == 0:
        return None
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    counts = np.asarray(counts, dtype=np.int64)
    indices = np.asarray(indices, dtype=np.int64)
    if len(indices) != int(counts.sum()) or (len(indices) and (indices.min() < 0 or indices.max() >= len(points))):
        raise ValueError(f"The USD mesh {mesh_prim.GetPath()} has face indices outside its points.")
    # Fan-triangulate every polygon; corner_of[i] is the index into face-varying data of corner i.
    starts = np.concatenate(([0], np.cumsum(counts)[:-1]))
    triangles, corners = [], []
    usable = counts >= 3
    for count_value in np.unique(counts[usable]):
        selected = starts[counts == count_value]
        for k in range(1, int(count_value) - 1):
            triangles.append(np.stack([indices[selected], indices[selected + k], indices[selected + k + 1]], axis=1))
            corners.append(np.stack([selected, selected + k, selected + k + 1], axis=1))
    if not triangles:
        return None
    faces = np.concatenate(triangles)
    corner_ids = np.concatenate(corners)
    if str(mesh_prim.GetOrientationAttr().Get()) == "leftHanded":
        faces = faces[:, ::-1]
        corner_ids = corner_ids[:, ::-1]
    uv, uv_interpolation = _read_uv(mesh_prim)
    vertices = points
    face_uv = None
    if uv is not None:
        if uv_interpolation == "faceVarying" and len(uv) == len(indices):
            # Unmerge: one vertex per corner so each keeps its own UV.
            vertices = points[faces.reshape(-1)]
            face_uv = uv[corner_ids.reshape(-1)]
            faces = np.arange(len(vertices), dtype=np.int64).reshape(-1, 3)
        elif uv_interpolation in ("vertex", "varying") and len(uv) == len(points):
            face_uv = uv
    vertices = (np.c_[vertices, np.ones(len(vertices))] @ world.T)[:, :3]
    if np.linalg.det(world[:3, :3]) < 0:
        faces = faces[:, ::-1]
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    color, image = _read_material(mesh_prim, textures, allowed_root)
    if face_uv is not None and image is not None:
        mesh.visual = trimesh.visual.TextureVisuals(
            uv=face_uv, material=trimesh.visual.material.PBRMaterial(
                baseColorTexture=image, baseColorFactor=color or [255, 255, 255, 255]))
    elif color is not None:
        mesh.visual = trimesh.visual.TextureVisuals(
            material=trimesh.visual.material.PBRMaterial(baseColorFactor=color))
    return mesh


def _read_uv(mesh_prim):
    from pxr import UsdGeom
    api = UsdGeom.PrimvarsAPI(mesh_prim)
    for name in ("st", "st0", "UVMap", "uv"):
        primvar = api.GetPrimvar(name)
        if primvar and primvar.HasValue():
            values = primvar.Get()
            if values is None:
                continue
            array = np.asarray(values, dtype=np.float64).reshape(-1, 2)
            if primvar.IsIndexed():
                array = array[np.asarray(primvar.GetIndices(), dtype=np.int64)]
            return array, str(primvar.GetInterpolation())
    return None, None


def _read_material(mesh_prim, textures, allowed_root=None):
    """``(rgba 0-255 list or None, PIL image or None)`` of the mesh's UsdPreviewSurface base colour."""
    from pxr import UsdShade, Ar
    try:
        material, _ = UsdShade.MaterialBindingAPI(mesh_prim).ComputeBoundMaterial()
    except Exception:  # noqa: BLE001
        return None, None
    if not material:
        return None, None
    shader = material.ComputeSurfaceSource()[0] if hasattr(material, "ComputeSurfaceSource") else None
    if not shader:
        return None, None
    diffuse = shader.GetInput("diffuseColor")
    if not diffuse:
        return None, None
    color = image = None
    source = diffuse.GetConnectedSource() if diffuse.HasConnectedSource() else None
    if source:
        texture_shader = UsdShade.Shader(source[0].GetPrim())
        file_input = texture_shader.GetInput("file")
        asset = file_input.Get() if file_input else None
        if asset is not None and asset.path:
            key = asset.resolvedPath or asset.path
            if key not in textures:
                textures[key] = _open_texture(asset, Ar, allowed_root)
            image = textures[key]
    else:
        value = diffuse.Get()
        if value is not None:
            color = [int(round(max(0.0, min(1.0, float(c))) * 255)) for c in value] + [255]
    return color, image


def _open_texture(asset, Ar, allowed_root=None):
    from PIL import Image
    import io
    resolved = asset.resolvedPath
    if not resolved:
        return None
    # Only a file inside the input's own directory (or inside the package) is read.
    if allowed_root and not os.path.realpath(str(resolved).split("[", 1)[0]).startswith(allowed_root + os.sep):
        return None
    try:
        buffer = Ar.GetResolver().OpenAsset(Ar.ResolvedPath(resolved))
        data = bytes(buffer.GetBuffer()) if buffer else None
        if not data:
            return None
        image = Image.open(io.BytesIO(data))
        image.load()
        return image
    except Exception:  # noqa: BLE001 - a texture that cannot be decoded is left out
        return None


# --------------------------------------------------------------------------------------- writer

def _identifier(name, used):
    """A valid, unique prim name."""
    from pxr import Tf
    base = Tf.MakeValidIdentifier(str(name)) or "geometry"
    candidate, number = base, 1
    while candidate in used:
        number += 1
        candidate = f"{base}_{number}"
    used.add(candidate)
    return candidate


def placed_reference(from_name, to_name, extension):
    """The relative asset path from the file ``from_name`` to the file ``to_name`` (both relative to one
    export folder, forward slashes) once the workflow's write-back has inserted ``extension`` before
    each file's own name; the plain relative path without an extension."""
    placed_from = apply_output_path_extension(from_name, extension)
    placed_to = apply_output_path_extension(to_name, extension)
    relative = posixpath.relpath(placed_to, posixpath.dirname(placed_from) or ".")
    return relative if relative.startswith(".") else "./" + relative


def write_usd(scene, output_file, meters_per_unit, up_axis, output_extension, input_name="model",
              output_path_extension=None):
    """Write ``scene`` (a ``trimesh.Scene``, ``Trimesh`` or ``PointCloud``) as USD to ``output_file``
    (``.usdc``, ``.usda`` or ``.usdz``). Textures are written beside a loose file and packed into a
    USDZ. ``output_path_extension`` is the run's output path prefix: a loose layer names its textures
    by where the write-back will put them, decided here so the layer is never opened again (a large
    layer reopened under the worker's memory limit does not fit)."""
    import trimesh
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, UsdUtils, Vt
    if not isinstance(scene, trimesh.Scene):
        scene = trimesh.Scene(scene)
    work_dir = os.path.dirname(os.path.abspath(output_file))
    if output_extension == ".usdz":
        # The layer and its textures are staged beside the export directory, not in it: everything in
        # the export directory is uploaded.
        package_dir = tempfile.mkdtemp(prefix="usdz_", dir=os.path.dirname(work_dir))
        layer_path = os.path.join(package_dir, os.path.splitext(os.path.basename(output_file))[0] + ".usdc")
    else:
        layer_path = output_file
    stage = Usd.Stage.CreateNew(layer_path)
    UsdGeom.SetStageMetersPerUnit(stage, float(meters_per_unit))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z if up_axis == "z" else UsdGeom.Tokens.y)
    root = UsdGeom.Xform.Define(stage, "/Model")
    stage.SetDefaultPrim(root.GetPrim())
    materials = UsdGeom.Scope.Define(stage, "/Model/Materials")
    # Textures are written beside the layer, as a glTF's buffers and an OBJ's material files are, so the
    # workflow's output path extension (inserted before each file's own name) keeps them together.
    texture_dir = os.path.dirname(layer_path)
    layer_name = os.path.basename(layer_path)
    reference_extension = None if output_extension == ".usdz" else output_path_extension
    used_nodes, used_materials, material_paths = set(), set(), {}
    count = 0
    for node_name in scene.graph.nodes_geometry:
        transform, geometry_name = scene.graph[node_name]
        geometry = scene.geometry[geometry_name]
        prim_name = _identifier(node_name, used_nodes)
        xform = UsdGeom.Xform.Define(stage, f"/Model/{prim_name}")
        if not np.allclose(transform, np.eye(4)):
            xform.AddTransformOp().Set(Gf.Matrix4d(np.asarray(transform, dtype=np.float64).T.tolist()))
        vertices = np.asarray(geometry.vertices, dtype=np.float64)
        if len(vertices) == 0:
            continue
        extent = Vt.Vec3fArray([Gf.Vec3f(*vertices.min(axis=0).tolist()), Gf.Vec3f(*vertices.max(axis=0).tolist())])
        path = f"/Model/{prim_name}/Geometry"
        if getattr(geometry, "faces", None) is not None and len(geometry.faces) > 0:
            usd_mesh = UsdGeom.Mesh.Define(stage, path)
            usd_mesh.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(vertices.astype(np.float32)))
            usd_mesh.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(np.full(len(geometry.faces), 3, dtype=np.int32)))
            usd_mesh.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(np.asarray(geometry.faces, dtype=np.int32).reshape(-1)))
            usd_mesh.CreateExtentAttr(extent)
            usd_mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
            usd_mesh.CreateOrientationAttr(UsdGeom.Tokens.rightHanded)
            # Not geometry.vertex_normals: without SciPy (as in this image) trimesh computes those
            # in O(vertices x faces); see vertex_normals.py.
            normals = vertex_normals.angle_weighted_vertex_normals(
                vertices, geometry.faces, geometry.face_normals).astype(np.float32)
            usd_mesh.CreateNormalsAttr(Vt.Vec3fArray.FromNumpy(normals))
            usd_mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
            _write_visual(stage, usd_mesh, geometry, materials, texture_dir, used_materials, material_paths,
                          layer_name, reference_extension,
                          Gf, Sdf, UsdGeom, UsdShade, Vt)
        else:
            points = UsdGeom.Points.Define(stage, path)
            points.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(vertices.astype(np.float32)))
            points.CreateWidthsAttr(Vt.FloatArray.FromNumpy(np.full(len(vertices), 0.01, dtype=np.float32)))
            points.CreateExtentAttr(extent)
        count += 1
    if count == 0:
        raise ValueError(f"{input_name} contains no geometry, so there is nothing to convert")
    stage.GetRootLayer().Save()
    del stage
    if output_extension == ".usdz":
        try:
            if not UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(layer_path), output_file):
                raise RuntimeError("The USDZ package could not be written.")
        finally:
            shutil.rmtree(package_dir, ignore_errors=True)


def _write_visual(stage, usd_mesh, geometry, materials, texture_dir, used_materials, material_paths,
                  layer_name, reference_extension,
                  Gf, Sdf, UsdGeom, UsdShade, Vt):
    visual = getattr(geometry, "visual", None)
    if visual is None or not getattr(visual, "defined", False):
        return
    material = getattr(visual, "material", None)
    uv = getattr(visual, "uv", None)
    color = [0.8, 0.8, 0.8]
    image = None
    if material is not None:
        base = getattr(material, "baseColorFactor", None)
        if base is None:
            base = getattr(material, "main_color", None)
        if base is not None:
            color = [float(c) / 255.0 for c in list(base)[:3]]
        image = getattr(material, "baseColorTexture", None)
        if image is None:
            image = getattr(material, "image", None)
    elif getattr(visual, "kind", None) in ("vertex", "face"):
        main = np.asarray(visual.main_color, dtype=np.float64)[:3] / 255.0
        color = main.tolist()
    key = (id(material), tuple(np.round(color, 4)), id(image))
    if key not in material_paths:
        name = _identifier(getattr(material, "name", None) or "material", used_materials)
        usd_material = UsdShade.Material.Define(stage, materials.GetPath().AppendChild(name))
        surface = UsdShade.Shader.Define(stage, usd_material.GetPath().AppendChild("PreviewSurface"))
        surface.CreateIdAttr("UsdPreviewSurface")
        surface.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.8)
        surface.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)
        diffuse = surface.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f)
        if image is not None and uv is not None:
            os.makedirs(texture_dir, exist_ok=True)
            file_name = f"{name}.png"
            image.convert("RGBA" if image.mode in ("RGBA", "LA", "P") else "RGB").save(os.path.join(texture_dir, file_name))
            reader = UsdShade.Shader.Define(stage, usd_material.GetPath().AppendChild("stReader"))
            reader.CreateIdAttr("UsdPrimvarReader_float2")
            reader.CreateInput("varname", Sdf.ValueTypeNames.String).Set("st")
            reader_out = reader.CreateOutput("result", Sdf.ValueTypeNames.Float2)
            texture = UsdShade.Shader.Define(stage, usd_material.GetPath().AppendChild("diffuseTexture"))
            texture.CreateIdAttr("UsdUVTexture")
            texture.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(
                Sdf.AssetPath(placed_reference(layer_name, file_name, reference_extension)))
            texture.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(reader_out)
            texture.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("sRGB")
            texture_out = texture.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
            diffuse.ConnectToSource(texture_out)
        else:
            diffuse.Set(Gf.Vec3f(*color))
        usd_material.CreateSurfaceOutput().ConnectToSource(surface.ConnectableAPI(), "surface")
        material_paths[key] = usd_material
    UsdShade.MaterialBindingAPI.Apply(usd_mesh.GetPrim()).Bind(material_paths[key])
    if uv is not None and len(uv) == len(geometry.vertices):
        primvar = UsdGeom.PrimvarsAPI(usd_mesh).CreatePrimvar(
            "st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex)
        primvar.Set(Vt.Vec2fArray.FromNumpy(np.asarray(uv, dtype=np.float32)))
