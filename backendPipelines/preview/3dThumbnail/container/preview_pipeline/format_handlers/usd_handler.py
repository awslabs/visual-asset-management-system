# Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
USD format handler for .usd, .usda, .usdc, .usdz files.
Loads USD geometry using pxr (OpenUSD Python bindings) and converts to PyVista for rendering.

The composed stage is walked the way a renderer sees it:

* ``Usd.PrimRange`` with ``Usd.TraverseInstanceProxies`` descends into instanceable prims, so
  geometry shared through native instancing is drawn at every instance;
* a ``UsdGeom.PointInstancer`` is expanded with ``ComputeInstanceTransformsAtTime``; its prototypes
  are drawn only through it;
* a prim whose computed visibility is ``invisible``, or whose computed purpose is ``guide`` or
  ``proxy``, is skipped with its subtree, so a guide or proxy coincident with the render geometry
  is not drawn on top of it;
* ``Mesh`` and the implicit gprims (``Cube``, ``Sphere``, ``Cylinder``, ``Cone``, ``Capsule``) are
  tessellated; n-gons are fan-triangulated in numpy;
* values are read at the stage's start time code when it has one, else at the earliest time
  sample, so a time-sampled-only attribute still has a value;
* colour per prim comes from the diffuse texture of the ``UsdUVTexture`` connected to the bound
  ``UsdPreviewSurface``'s ``diffuseColor``, else that input's constant value, else ``displayColor``
  (indexed primvars flattened; constant, uniform, vertex/varying and faceVarying interpolation).
  A prim with no colour in a stage where others have one gets a neutral grey instead of dropping
  every prim's colour.

Reference: https://github.com/beersandrew/usd-thumbnail-generator
"""

import io
import os
import zipfile
import numpy as np
import pyvista as pv
from PIL import Image
from ..utils.logging import get_logger

logger = get_logger()

SUPPORTED_EXTENSIONS = {".usd", ".usda", ".usdc", ".usdz"}

# Colour of a prim that carries none, in a stage where other prims are coloured.
NEUTRAL_RGB = (190, 190, 190)
# Upper bound on the vertices one stage expands to (instancing can multiply a small file).
MAX_POINTS = 30_000_000
_RENDERED_PURPOSES = ("default", "render")
_IMPLICIT_RESOLUTION = 24


def can_handle(extension: str) -> bool:
    return extension.lower() in SUPPORTED_EXTENSIONS


def load(file_path: str, asset_root: str = None) -> pv.PolyData:
    """
    Load a USD file and return a PyVista PolyData object for rendering.
    Supports: .usd, .usda, .usdc, .usdz

    ``asset_root`` is the local directory the input's referenced files were fetched into; texture
    files are read only from inside it (default: the input file's directory).
    """
    from pxr import Usd, UsdGeom

    ext = os.path.splitext(file_path)[1].lower()
    logger.info(f"Loading USD file: {file_path} (format: {ext})")

    stage = Usd.Stage.Open(file_path)
    if stage is None:
        raise ValueError(f"Failed to open USD stage: {file_path}")

    time = _read_time(stage)
    xform_cache = UsdGeom.XformCache(time)
    context = _LoadContext(file_path, asset_root or os.path.dirname(os.path.abspath(file_path)), time)

    parts = []
    iterator = iter(Usd.PrimRange(stage.GetPseudoRoot(), Usd.TraverseInstanceProxies()))
    for prim in iterator:
        if prim.IsPseudoRoot():
            continue
        if not _is_rendered(prim, time):
            iterator.PruneChildren()
            continue
        if prim.IsA(UsdGeom.PointInstancer):
            parts.extend(_expand_point_instancer(prim, xform_cache, context))
            iterator.PruneChildren()
            continue
        part = _gprim_part(prim, np.array(xform_cache.GetLocalToWorldTransform(prim)), context)
        if part is not None:
            parts.append(part)
        if context.points_total > MAX_POINTS:
            logger.warning(f"Stage expands past {MAX_POINTS} vertices; remaining prims are skipped")
            break

    if not parts:
        raise ValueError("No valid mesh geometry found in USD stage")

    pv_mesh = _merge_parts(parts)
    pv_mesh = _normalize_usd_up_axis(stage, pv_mesh)
    logger.info(f"Loaded USD model: {pv_mesh.n_points} vertices, {pv_mesh.n_cells} faces "
                f"from {len(parts)} prims")
    return pv_mesh


class _LoadContext:
    def __init__(self, file_path, asset_root, time):
        self.file_path = file_path
        self.asset_root = os.path.realpath(asset_root)
        self.time = time
        self.textures = {}
        self.points_total = 0


def _read_time(stage):
    """The time to read the stage at: its start time code when it has an authored range, else the
    earliest time sample of each attribute (its default when it has no samples)."""
    from pxr import Usd

    if stage.HasAuthoredTimeCodeRange():
        return Usd.TimeCode(stage.GetStartTimeCode())
    return Usd.TimeCode.EarliestTime()


def _is_rendered(prim, time) -> bool:
    """False for a prim a final render does not draw: invisible, or guide/proxy purpose."""
    from pxr import UsdGeom

    imageable = UsdGeom.Imageable(prim)
    if not imageable:
        return True
    if imageable.ComputeVisibility(time) == UsdGeom.Tokens.invisible:
        return False
    return str(imageable.ComputePurpose()) in _RENDERED_PURPOSES


# --------------------------------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------------------------------

def _gprim_part(prim, matrix, context):
    """(points, triangles, colours or None) for one gprim in world space, or None."""
    from pxr import UsdGeom

    if prim.IsA(UsdGeom.Mesh):
        geometry = _mesh_geometry(prim, context)
    elif prim.IsA(UsdGeom.Gprim):
        geometry = _implicit_geometry(prim, context)
    else:
        return None
    if geometry is None:
        return None
    points, triangles, colours = geometry
    points = _transform(points, matrix)
    context.points_total += len(points)
    return points, triangles, colours


def _transform(points, matrix):
    """Apply a USD 4x4 matrix (row-vector convention, p' = p * M) to (N, 3) points."""
    matrix = np.asarray(matrix, dtype=np.float64)
    return points @ matrix[:3, :3] + matrix[3, :3]


def _mesh_geometry(prim, context):
    from pxr import UsdGeom

    mesh = UsdGeom.Mesh(prim)
    time = context.time
    points = mesh.GetPointsAttr().Get(time)
    counts = mesh.GetFaceVertexCountsAttr().Get(time)
    indices = mesh.GetFaceVertexIndicesAttr().Get(time)
    if points is None or counts is None or indices is None or len(points) == 0:
        logger.warning(f"Skipping prim {prim.GetPath()}: empty geometry data")
        return None

    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    counts = np.asarray(counts, dtype=np.int64)
    indices = np.asarray(indices, dtype=np.int64)
    triangles, tri_face, tri_corners = fan_triangulate(counts, indices, len(points))
    if len(triangles) == 0:
        logger.warning(f"Skipping prim {prim.GetPath()}: no triangles after triangulation")
        return None

    topology = _Topology(counts, indices, len(points))
    colours = _texture_colours(prim, topology, context)
    if colours is None:
        colours = _material_base_colour(prim, len(points))
    if colours is None:
        colours = _primvar_colours(prim, "displayColor", topology, time)
    logger.info(f"  Prim {prim.GetPath()}: {len(points)} vertices, {len(triangles)} triangles")
    return points, triangles, colours


class _Topology:
    """A mesh's face layout, for mapping primvars of any interpolation onto its vertices."""

    def __init__(self, counts, indices, n_points):
        self.counts = counts
        self.indices = indices
        self.n_points = n_points
        self.n_faces = len(counts)
        # The face each face-vertex belongs to, for uniform (per-face) primvars.
        valid = min(int(counts.sum()) if len(counts) else 0, len(indices))
        self.face_of_corner = np.repeat(np.arange(len(counts)), counts)[:valid]
        self.corner_vertices = indices[:valid]


def fan_triangulate(counts, indices, n_points):
    """Fan-triangulate polygon faces, vectorised.

    Returns (triangles (T, 3) vertex indices, the face of each triangle (T,), the face-vertex index
    of each triangle corner (T, 3)). A face with fewer than three vertices, a face running past the
    end of ``indices``, and a triangle naming a vertex outside ``n_points`` are dropped.
    """
    counts = np.asarray(counts, dtype=np.int64)
    indices = np.asarray(indices, dtype=np.int64)
    if len(counts) == 0:
        return np.empty((0, 3), np.int64), np.empty(0, np.int64), np.empty((0, 3), np.int64)
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
    in_bounds = starts + counts <= len(indices)
    if not in_bounds.all():
        logger.warning(f"Face vertex indices end early ({len(indices)} entries); "
                       f"{int((~in_bounds).sum())} faces dropped")
    per_face = np.where((counts >= 3) & in_bounds, counts - 2, 0)
    face = np.repeat(np.arange(len(counts)), per_face)
    first = np.repeat(np.cumsum(per_face) - per_face, per_face)
    k = np.arange(len(face)) - first
    start = starts[face]
    corners = np.stack([start, start + k + 1, start + k + 2], axis=1)
    triangles = indices[corners]
    keep = np.all((triangles >= 0) & (triangles < n_points), axis=1)
    if not keep.all():
        logger.warning(f"{int((~keep).sum())} triangles name vertices outside the mesh; dropped")
    return triangles[keep], face[keep], corners[keep]


def _implicit_geometry(prim, context):
    """Tessellate a Cube, Sphere, Cylinder, Cone or Capsule in its local space."""
    from pxr import UsdGeom

    time = context.time
    shape = None
    if prim.IsA(UsdGeom.Cube):
        size = float(UsdGeom.Cube(prim).GetSizeAttr().Get(time) or 2.0)
        shape = pv.Cube(x_length=size, y_length=size, z_length=size)
    elif prim.IsA(UsdGeom.Sphere):
        radius = float(UsdGeom.Sphere(prim).GetRadiusAttr().Get(time) or 1.0)
        shape = pv.Sphere(radius=radius, theta_resolution=_IMPLICIT_RESOLUTION,
                          phi_resolution=_IMPLICIT_RESOLUTION // 2)
    elif prim.IsA(UsdGeom.Cylinder) or prim.IsA(UsdGeom.Cone) or prim.IsA(UsdGeom.Capsule):
        schema = (UsdGeom.Cylinder if prim.IsA(UsdGeom.Cylinder)
                  else UsdGeom.Cone if prim.IsA(UsdGeom.Cone) else UsdGeom.Capsule)(prim)
        radius = float(schema.GetRadiusAttr().Get(time) or 1.0)
        height = float(schema.GetHeightAttr().Get(time) or 2.0)
        axis = str(schema.GetAxisAttr().Get(time) or "Z")
        direction = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0)}.get(axis, (0.0, 0.0, 1.0))
        if prim.IsA(UsdGeom.Cone):
            shape = pv.Cone(direction=direction, height=height, radius=radius,
                            resolution=_IMPLICIT_RESOLUTION)
        else:
            shape = pv.Cylinder(direction=direction, radius=radius, height=height,
                                resolution=_IMPLICIT_RESOLUTION, capping=True)
            if prim.IsA(UsdGeom.Capsule):
                offset = np.asarray(direction) * height / 2.0
                for sign in (1.0, -1.0):
                    shape = shape.merge(pv.Sphere(radius=radius, center=tuple(sign * offset),
                                                  theta_resolution=_IMPLICIT_RESOLUTION,
                                                  phi_resolution=_IMPLICIT_RESOLUTION // 2))
    if shape is None:
        return None
    shape = shape.triangulate().clean()
    points = np.asarray(shape.points, dtype=np.float64)
    triangles = np.asarray(shape.faces).reshape(-1, 4)[:, 1:].astype(np.int64)
    colours = _material_base_colour(prim, len(points))
    if colours is None:
        colours = _constant_primvar_colour(prim, "displayColor", len(points), time)
    logger.info(f"  Prim {prim.GetPath()}: implicit {prim.GetTypeName()}, {len(points)} vertices")
    return points, triangles, colours


def _expand_point_instancer(prim, xform_cache, context):
    """Every instance of a PointInstancer, each prototype's gprims placed by its transform."""
    from pxr import Usd, UsdGeom

    instancer = UsdGeom.PointInstancer(prim)
    time = context.time
    transforms = instancer.ComputeInstanceTransformsAtTime(time, time)
    proto_indices = instancer.GetProtoIndicesAttr().Get(time)
    targets = instancer.GetPrototypesRel().GetTargets()
    if not transforms or proto_indices is None or not targets:
        return []
    instancer_to_world = np.array(xform_cache.GetLocalToWorldTransform(prim))
    stage = prim.GetStage()

    # Each prototype's gprims in the prototype root's space, tessellated once.
    prototypes = []
    for target in targets:
        root = stage.GetPrimAtPath(target)
        geometry = []
        if root:
            for child in Usd.PrimRange(root, Usd.TraverseInstanceProxies()):
                if not _is_rendered(child, time):
                    continue
                relative, _ = xform_cache.ComputeRelativeTransform(child, root)
                part = _gprim_part(child, np.array(relative), context)
                if part is not None:
                    geometry.append(part)
        prototypes.append(geometry)

    proto_indices = np.asarray(proto_indices, dtype=np.int64)
    parts = []
    for instance, matrix in zip(proto_indices, transforms):
        if instance < 0 or instance >= len(prototypes):
            continue
        world = np.array(matrix) @ instancer_to_world
        for points, triangles, colours in prototypes[instance]:
            if context.points_total > MAX_POINTS:
                logger.warning(f"Point instancer {prim.GetPath()}: vertex limit reached, "
                               f"remaining instances skipped")
                return parts
            parts.append((_transform(points, world), triangles, colours))
            context.points_total += len(points)
    logger.info(f"  Point instancer {prim.GetPath()}: {len(proto_indices)} instances of "
                f"{len(prototypes)} prototypes")
    return parts


def _merge_parts(parts):
    """One PolyData from (points, triangles, colours) parts; colours kept when any part has some."""
    offsets = np.cumsum([0] + [len(points) for points, _, _ in parts[:-1]])
    vertices = np.vstack([points for points, _, _ in parts])
    triangles = np.vstack([tris + offset for (_, tris, _), offset in zip(parts, offsets)])
    faces = np.column_stack([np.full(len(triangles), 3, dtype=np.int64), triangles]).ravel()
    pv_mesh = pv.PolyData(vertices, faces)
    if any(colours is not None for _, _, colours in parts):
        neutral = np.asarray(NEUTRAL_RGB, dtype=np.uint8)
        pv_mesh.point_data["RGB"] = np.vstack([
            colours if colours is not None else np.tile(neutral, (len(points), 1))
            for points, _, colours in parts]).astype(np.uint8)
    return pv_mesh


# --------------------------------------------------------------------------------------------------
# Colour
# --------------------------------------------------------------------------------------------------

def _to_rgb8(values):
    values = np.asarray(values, dtype=np.float64)
    if values.ndim == 1:
        values = values.reshape(1, -1)
    if values.shape[-1] < 3:
        return None
    return (np.clip(values[:, :3], 0.0, 1.0) * 255).astype(np.uint8)


def _flattened_primvar(prim, name, time):
    """(values, interpolation) of a primvar with its indices applied, or (None, None)."""
    from pxr import UsdGeom

    primvar = UsdGeom.PrimvarsAPI(prim).GetPrimvar(name)
    if not primvar or not primvar.HasValue():
        return None, None
    values = primvar.ComputeFlattened(time)
    if values is None or len(values) == 0:
        return None, None
    return np.asarray(values, dtype=np.float64), str(primvar.GetInterpolation())


def _to_vertices(values, interpolation, topology):
    """Map per-element primvar values onto the mesh's vertices (averaging shared corners)."""
    n = topology.n_points
    if interpolation == "constant" or len(values) == 1:
        return np.tile(values[:1], (n, 1))
    if interpolation in ("vertex", "varying") and len(values) == n:
        return values
    if interpolation == "uniform" and len(values) == topology.n_faces:
        corner_values = values[topology.face_of_corner]
    elif interpolation == "faceVarying" and len(values) >= len(topology.corner_vertices):
        corner_values = values[:len(topology.corner_vertices)]
    else:
        logger.warning(f"    primvar has {len(values)} {interpolation} values for "
                       f"{n} vertices / {topology.n_faces} faces; ignored")
        return None
    sums = np.zeros((n, values.shape[1]), dtype=np.float64)
    counts = np.zeros(n, dtype=np.int64)
    valid = (topology.corner_vertices >= 0) & (topology.corner_vertices < n)
    np.add.at(sums, topology.corner_vertices[valid], corner_values[valid])
    np.add.at(counts, topology.corner_vertices[valid], 1)
    seen = counts > 0
    sums[seen] /= counts[seen, None]
    return sums


def _primvar_colours(prim, name, topology, time):
    values, interpolation = _flattened_primvar(prim, name, time)
    if values is None:
        return None
    if values.ndim == 1:
        values = values.reshape(-1, 1)
    if values.shape[1] < 3:
        return None
    mapped = _to_vertices(values, interpolation, topology)
    return None if mapped is None else _to_rgb8(mapped)


def _constant_primvar_colour(prim, name, n_points, time):
    values, _ = _flattened_primvar(prim, name, time)
    if values is None:
        return None
    rgb = _to_rgb8(values[:1])
    return None if rgb is None else np.tile(rgb, (n_points, 1))


def _preview_surface_diffuse(prim):
    """The diffuseColor input of the UsdPreviewSurface bound to ``prim``, or None."""
    from pxr import Usd, UsdShade

    try:
        material, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
    except Exception:
        return None
    if not material or not material.GetPrim().IsValid():
        return None
    for desc in Usd.PrimRange(material.GetPrim()):
        shader = UsdShade.Shader(desc)
        id_attr = shader.GetIdAttr() if shader else None
        if id_attr and id_attr.Get() == "UsdPreviewSurface":
            diffuse = shader.GetInput("diffuseColor")
            if diffuse:
                return diffuse
    return None


def _material_base_colour(prim, n_points):
    """The bound UsdPreviewSurface's constant diffuseColor, broadcast; None when it has none or it
    is connected (a texture)."""
    diffuse = _preview_surface_diffuse(prim)
    if diffuse is None or diffuse.HasConnectedSource():
        return None
    value = diffuse.Get()
    if value is None:
        return None
    rgb = _to_rgb8(np.asarray(value, dtype=np.float64)[:3])
    if rgb is None:
        return None
    logger.info(f"    Material base color: {rgb[0].tolist()}")
    return np.tile(rgb, (n_points, 1))


def _diffuse_texture(prim):
    """(texture asset path value, UV primvar name) of the UsdUVTexture connected to the bound
    UsdPreviewSurface's diffuseColor, or (None, None). Other textures of the material (normal,
    roughness, occlusion maps) are not colour and are never used."""
    from pxr import UsdShade

    diffuse = _preview_surface_diffuse(prim)
    if diffuse is None or not diffuse.HasConnectedSource():
        return None, None
    source = diffuse.GetConnectedSource()
    if not source or not source[0]:
        return None, None
    texture = UsdShade.Shader(source[0].GetPrim())
    id_attr = texture.GetIdAttr() if texture else None
    if not id_attr or id_attr.Get() != "UsdUVTexture":
        return None, None
    file_input = texture.GetInput("file")
    if not file_input:
        return None, None
    uv_name = None
    st_input = texture.GetInput("st")
    if st_input and st_input.HasConnectedSource():
        reader = UsdShade.Shader(st_input.GetConnectedSource()[0].GetPrim())
        varname = reader.GetInput("varname") if reader else None
        if varname and varname.Get():
            uv_name = str(varname.Get())
    return file_input.Get(), uv_name


_UV_NAMES = ("st", "UVMap", "st0", "texCoord_0", "Texture_uv", "uv")


def _uv_primvar(prim, preferred, time):
    names = ([preferred] if preferred else []) + [n for n in _UV_NAMES if n != preferred]
    for name in names:
        values, interpolation = _flattened_primvar(prim, name, time)
        if values is not None and values.ndim == 2 and values.shape[1] >= 2:
            return values[:, :2], interpolation
    return None, None


def _texture_colours(prim, topology, context):
    """Per-vertex colours sampled from the diffuse texture, or None."""
    try:
        asset_path, uv_name = _diffuse_texture(prim)
        if asset_path is None:
            return None
        uvs, interpolation = _uv_primvar(prim, uv_name, context.time)
        if uvs is None:
            return None
        image = _load_texture(asset_path, context)
        if image is None:
            return None
        texels = np.asarray(image)
        h, w = texels.shape[:2]
        px = np.clip((uvs[:, 0] % 1.0) * (w - 1), 0, w - 1).astype(np.int64)
        py = np.clip((1.0 - uvs[:, 1] % 1.0) * (h - 1), 0, h - 1).astype(np.int64)
        sampled = texels[py, px, :3].astype(np.float64) / 255.0
        mapped = _to_vertices(sampled, interpolation, topology)
        if mapped is None:
            return None
        logger.info(f"    Baked texture colors from {os.path.basename(_asset_path_text(asset_path))}")
        return _to_rgb8(mapped)
    except Exception as e:
        logger.debug(f"    Texture extraction failed: {e}")
        return None


def _asset_path_text(asset_path):
    return getattr(asset_path, "path", None) or str(asset_path).strip("@")


def _load_texture(asset_path, context):
    """The texture image (RGB) named by an SdfAssetPath, read only from inside the asset root."""
    authored = getattr(asset_path, "path", "") or ""
    resolved = getattr(asset_path, "resolvedPath", "") or ""
    key = resolved or authored
    if key in context.textures:
        return context.textures[key]
    image = None
    if os.path.splitext(context.file_path)[1].lower() == ".usdz":
        image = _load_texture_from_usdz(context.file_path, authored or resolved)
    else:
        candidates = []
        if resolved and "[" not in resolved:
            candidates.append(resolved)
        if authored and not os.path.isabs(authored):
            candidates.append(os.path.join(os.path.dirname(context.file_path), authored))
        for candidate in candidates:
            real = os.path.realpath(candidate)
            if not real.startswith(context.asset_root + os.sep) or not os.path.isfile(real):
                continue
            try:
                image = Image.open(real).convert("RGB")
                break
            except Exception as e:
                logger.warning(f"    Could not read texture {authored}: {e}")
        if image is None and "[" in resolved:
            package, inner = resolved.split("[", 1)
            real = os.path.realpath(package)
            if real.startswith(context.asset_root + os.sep):
                image = _load_texture_from_usdz(real, inner.rstrip("]"))
    if image is None:
        logger.warning(f"    Could not load texture: {authored or resolved}")
    context.textures[key] = image
    return image


def _load_texture_from_usdz(usdz_path, texture_rel_path):
    """
    Load a texture image from inside a USDZ archive.
    USDZ is an uncompressed zip containing a .usdc scene + texture files.
    """
    try:
        with zipfile.ZipFile(usdz_path, "r") as zf:
            names = zf.namelist()
            clean_path = texture_rel_path.lstrip("./")

            # Strategy 1: Exact path match
            if clean_path in names:
                with zf.open(clean_path) as f:
                    return Image.open(io.BytesIO(f.read())).convert("RGB")

            # Strategy 2: Basename match (handles path prefix differences)
            basename = os.path.basename(clean_path)
            for name in names:
                if os.path.basename(name) == basename:
                    with zf.open(name) as f:
                        return Image.open(io.BytesIO(f.read())).convert("RGB")

    except Exception as e:
        logger.warning(f"    Failed to extract texture from USDZ: {e}")

    return None


def _normalize_usd_up_axis(stage, pv_data):
    """
    Detect up-axis from USD stage metadata and rotate Z-up to Y-up if needed.
    USD stages declare their up-axis via UsdGeom.GetStageUpAxis().
    """
    from pxr import UsdGeom

    up_axis = UsdGeom.GetStageUpAxis(stage)
    logger.info(f"USD stage up-axis: {up_axis}")

    if up_axis == UsdGeom.Tokens.z:
        logger.info("USD stage is Z-up, rotating to Y-up for rendering")
        points = pv_data.points.copy()
        new_points = np.empty_like(points)
        new_points[:, 0] = points[:, 0]    # X stays
        new_points[:, 1] = points[:, 2]    # Y = old Z (up axis)
        new_points[:, 2] = -points[:, 1]   # Z = -old Y
        pv_data.points = new_points
    else:
        logger.info(f"USD stage is {up_axis}-up (no rotation needed)")

    return pv_data
