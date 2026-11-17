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
  ``UsdPreviewSurface``'s ``diffuseColor``, else that input's constant value; when the bound material
  has no ``UsdPreviewSurface``, from its NVIDIA ``OmniPBR`` or ``OmniSurface`` MDL shader (diffuse
  texture, else base colour constant, times the OmniPBR tint), or from another MDL module's inputs
  named like an Unreal Engine export's (``AlbedoTexture``, ``ColorAlbedo``, ``BaseColor_Tint``; an
  approximation); else ``displayColor`` (indexed
  primvars flattened; constant, uniform, vertex/varying and faceVarying interpolation).
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
from .. import resources, scene_parts
from ..utils.logging import get_logger

logger = get_logger()

SUPPORTED_EXTENSIONS = {".usd", ".usda", ".usdc", ".usdz"}

# A texture read out of a USDZ archive is decompressed in memory; a larger member is skipped.
MAX_USDZ_TEXTURE_BYTES = 512 * 1024 ** 2
# A texture is decoded at most MAX_TEXTURE_SIDE pixels on its long side: a thumbnail samples one texel
# per vertex, which a 1024-pixel texture serves as a 4096-pixel one does, in 3 MiB of RGB instead of
# 48 MiB. The decoded textures one stage holds while it loads take at most TEXTURE_MEMORY_FRACTION of
# the task's memory (``texture_cache_limit``); a texture past that is not read.
MAX_TEXTURE_SIDE = 1024
TEXTURE_MEMORY_FRACTION = 0.1
# Connections followed from a shader input to the value it reads (material interface inputs, node
# graph outputs); a longer chain, or one that loops, is not read.
MAX_CONNECTION_HOPS = 32
# Entries named in each end-of-load colour summary, most prims first; the rest are counted.
MAX_SUMMARY_ENTRIES = 10

# Colour of a prim that carries none, in a stage where other prims are coloured.
NEUTRAL_RGB = (190, 190, 190)
# Upper bound on the vertices one stage expands to (instancing can multiply a small file).
MAX_POINTS = 30_000_000
_RENDERED_PURPOSES = ("default", "render")
_IMPLICIT_RESOLUTION = 24

# MDL material families whose base colour is read from the shader's inputs, by the lower-case file
# name prefix of the module (OmniPBR.mdl, OmniPBR_ClearCoat.mdl, OmniSurface.mdl, OmniSurfaceLite.mdl):
# (diffuse texture input, base colour constant input, tint input or None). The texture replaces the
# constant; the tint multiplies either.
_MDL_DIFFUSE_INPUTS = (
    ("omnipbr", "diffuse_texture", "diffuse_color_constant", "diffuse_tint"),
    ("omnisurface", "diffuse_reflection_color_image", "diffuse_reflection_color", None),
)
# Any other MDL module, such as the MI_*.mdl materials of an Unreal Engine export (OmniUe4Base), is
# read by these input names, first match wins. Such a module combines them in its own
# expression (MI_CeilingA_06b.mdl: lerp(ColorAlbedo, AlbedoTexture, MaskSelection)), which is not
# evaluated, so the colour is an approximation: the texture, else the constant, times the tint.
_MDL_NAMED_TEXTURES = ("AlbedoTexture", "BaseColor_Texture", "BaseColorTexture", "DiffuseTexture")
_MDL_NAMED_CONSTANTS = ("ColorAlbedo", "BaseColor", "DiffuseColor")
_MDL_NAMED_TINTS = ("BaseColor_Tint", "AlbedoTint")


def can_handle(extension: str) -> bool:
    return extension.lower() in SUPPORTED_EXTENSIONS


def texture_cache_limit(memory_bytes: int) -> int:
    """The bytes of decoded textures one stage may hold while it loads, in a task with ``memory_bytes``
    of memory."""
    return int(memory_bytes * TEXTURE_MEMORY_FRACTION)


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
    if any(context.mdl_unread.values()):
        logger.info(f"{sum(context.mdl_unread.values())} prims are not coloured by their MDL material: "
                    + _counted(context.mdl_unread))
    if any(context.mdl_approximated.values()):
        logger.info(f"{sum(context.mdl_approximated.values())} prims are coloured by an approximation of "
                    "their MDL module's colour: "
                    + _counted({repr(module): count for module, count in context.mdl_approximated.items()}))
    if context.textures_over_budget:
        logger.warning(f"Textures not read, past the {context.texture_budget / 1024 ** 2:.1f} MiB of decoded "
                       f"textures the stage may hold: {context.textures_over_budget}")

    # A backdrop (a ground or environment plane) is dropped before the parts are merged, so the
    # prims kept take their colours as a stage holding them alone would.
    keep = scene_parts.backdrop_keep([points for points, _, _ in parts])
    if keep is not None:
        parts = [part for part, kept in zip(parts, keep) if kept]
    pv_mesh = _merge_parts(parts)
    pv_mesh = _normalize_usd_up_axis(stage, pv_mesh)
    scene_parts.record_parts(pv_mesh, [len(points) for points, _, _ in parts], backdrops_dropped=True)
    logger.info(f"Loaded USD model: {pv_mesh.n_points} vertices, {pv_mesh.n_cells} faces "
                f"from {len(parts)} prims")
    return pv_mesh


class _LoadContext:
    def __init__(self, file_path, asset_root, time):
        self.file_path = file_path
        self.asset_root = os.path.realpath(asset_root)
        self.time = time
        # Decoded textures by path, the bytes they hold, the most they may hold, and the textures not
        # read because they would pass it.
        self.textures = {}
        self.texture_bytes = 0
        self.texture_budget = texture_cache_limit(resources.memory_limit_bytes())
        self.textures_over_budget = 0
        self.mdl_materials = {}
        # Prims drawn without their material's colour, by the reason it could not be read.
        self.mdl_unread = {}
        # Prims coloured by an approximation of their MDL module's colour, by module.
        self.mdl_approximated = {}
        # Why a shader input's connections were not followed; each reason is logged once.
        self.connections_not_followed = set()
        self.points_total = 0


def _counted(counts):
    """'<count> with <key>' for each entry of ``counts``, most prims first, at most MAX_SUMMARY_ENTRIES
    of them, then how many more entries and prims there are."""
    entries = sorted(((key, count) for key, count in counts.items() if count), key=lambda entry: -entry[1])
    text = "; ".join(f"{count} with {key}" for key, count in entries[:MAX_SUMMARY_ENTRIES])
    rest = entries[MAX_SUMMARY_ENTRIES:]
    if rest:
        text += f"; and {len(rest)} more ({sum(count for _, count in rest)} prims)"
    return text


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
        colours = _mdl_colours(prim, topology, context, len(points))
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
        colours = _mdl_colours(prim, None, context, len(points))
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


def _bound_material(prim):
    """The material bound to ``prim``, or None."""
    from pxr import UsdShade

    try:
        material, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
    except Exception:
        return None
    if not material or not material.GetPrim().IsValid():
        return None
    return material


def _is_preview_surface(shader):
    id_attr = shader.GetIdAttr() if shader else None
    return bool(id_attr) and id_attr.Get() == "UsdPreviewSurface"


def _preview_surface_diffuse(prim):
    """The diffuseColor input of the UsdPreviewSurface bound to ``prim``, or None."""
    from pxr import Usd, UsdShade

    material = _bound_material(prim)
    if material is None:
        return None
    for desc in Usd.PrimRange(material.GetPrim()):
        shader = UsdShade.Shader(desc)
        if _is_preview_surface(shader):
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
    """(uvs, interpolation) of the prim's texture coordinates, or (None, None): the primvar
    ``preferred`` names, then the usual UV set names, then the prim's other ``texCoord2f[]`` primvars
    by name (an exporter may name its sets ``st_0``, ``st_1``, ...)."""
    names = ([preferred] if preferred else []) + [n for n in _UV_NAMES if n != preferred]
    names += _other_texture_coordinate_primvars(prim, names)
    for name in names:
        values, interpolation = _flattened_primvar(prim, name, time)
        if values is not None and values.ndim == 2 and values.shape[1] >= 2:
            return values[:, :2], interpolation
    return None, None


def _other_texture_coordinate_primvars(prim, named):
    """Names of the prim's texture coordinate array primvars that ``named`` does not list, sorted."""
    from pxr import UsdGeom

    found = []
    for primvar in UsdGeom.PrimvarsAPI(prim).GetPrimvarsWithValues():
        type_name = primvar.GetTypeName()
        name = str(primvar.GetPrimvarName())
        if type_name.isArray and type_name.role == "TextureCoordinate" and name not in named:
            found.append(name)
    return sorted(found)


def _texture_colours(prim, topology, context):
    """Per-vertex colours sampled from the diffuse texture, or None."""
    try:
        asset_path, uv_name = _diffuse_texture(prim)
    except Exception as e:
        logger.debug(f"    Texture extraction failed: {e}")
        return None
    if asset_path is None:
        return None
    sampled = _sample_texture(prim, asset_path, uv_name, topology, context)
    return None if sampled is None else _to_rgb8(sampled)


def _sample_texture(prim, asset_path, uv_name, topology, context):
    """Per-vertex RGB in 0..1 sampled from the texture ``asset_path`` names at the prim's UVs, or None
    (no UVs, or the texture is not readable from inside the asset root)."""
    try:
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
        return mapped
    except Exception as e:
        logger.debug(f"    Texture extraction failed: {e}")
        return None


class _MdlDiffuse:
    """The base colour inputs of a material's MDL shader, as authored, with the input names that set
    them, its module's file name and the material's path. ``unread`` names why the material's colour is
    not read (its module and the reason), with no inputs set; ``approximated`` is True when the inputs
    were found by name in a module whose own colour expression is not evaluated."""

    def __init__(self, module, material_path, texture=None, constant=None, tint=None,
                 names=(None, None, None), unread=None, approximated=False):
        self.module = module
        self.material_path = material_path
        self.texture = texture
        self.constant = constant
        self.tint = tint
        self.texture_name, self.constant_name, self.tint_name = names
        self.unread = unread
        self.approximated = approximated


def _mdl_colours(prim, topology, context, n_points):
    """Per-vertex colours from the MDL shader of the material bound to ``prim``, read by
    ``_read_mdl_diffuse``: its diffuse texture sampled at the prim's UVs (not for an implicit gprim,
    which has none, ``topology`` None), else its base colour constant, times its tint. None when the
    material has a UsdPreviewSurface (that path owns it) or no MDL shader, or when no colour comes from
    it; such a prim is counted under the reason, and a prim coloured by an approximation under its
    module (``_count_unread``, ``_count_approximated``)."""
    diffuse = _mdl_diffuse(prim, context)
    if diffuse is None:
        return None
    if diffuse.unread is not None:
        _count_unread(context, diffuse.material_path, diffuse.unread)
        return None
    colours, used = None, []
    if diffuse.texture is not None and topology is not None:
        colours = _sample_texture(prim, diffuse.texture, None, topology, context)
        used = [diffuse.texture_name]
    if colours is None and diffuse.constant is not None:
        colours = np.tile(diffuse.constant, (n_points, 1))
        used = [diffuse.constant_name]
    if colours is None:
        _count_unread(context, diffuse.material_path,
                      f"{diffuse.module!r}, whose {diffuse.texture_name} could not be sampled (unreadable, "
                      f"past the stage's texture budget, or no UVs) and which sets no base colour constant")
        return None
    if diffuse.tint is not None:
        colours = colours * diffuse.tint
        used.append(diffuse.tint_name)
    if diffuse.approximated:
        _count_approximated(context, diffuse, used)
    return _to_rgb8(colours)


def _mdl_diffuse(prim, context):
    """The ``_MdlDiffuse`` of the material bound to ``prim``, read once per material, or None."""
    material = _bound_material(prim)
    if material is None:
        return None
    material_prim = material.GetPrim()
    if material_prim.IsInstanceProxy():
        material_prim = material_prim.GetPrimInPrototype()
    key = str(material_prim.GetPath())
    if key not in context.mdl_materials:
        context.mdl_materials[key] = _read_mdl_diffuse(material, context)
    return context.mdl_materials[key]


def _read_mdl_diffuse(material, context):
    """The base colour inputs of ``material``'s MDL surface shader when the material has no
    UsdPreviewSurface: by family for OmniPBR and OmniSurface, by input name for any other module;
    else None, or an ``unread`` entry when the module sets none of them."""
    from pxr import Usd, UsdShade

    shaders = [UsdShade.Shader(desc) for desc in Usd.PrimRange(material.GetPrim())]
    shaders = [shader for shader in shaders if shader]
    if any(_is_preview_surface(shader) for shader in shaders):
        return None
    shader = _mdl_surface_shader(material, shaders)
    if shader is None:
        return None
    module = os.path.basename(_mdl_source_asset(shader))
    family = next((inputs for inputs in _MDL_DIFFUSE_INPUTS if module.lower().startswith(inputs[0])), None)
    if family is None:
        return _read_named_mdl_diffuse(shader, material, module, context)
    _, texture_name, constant_name, tint_name = family
    texture = _asset_value(_input_value(shader, texture_name, context))
    constant = _colour_value(_input_value(shader, constant_name, context))
    tint = _colour_value(_input_value(shader, tint_name, context)) if tint_name else None
    if texture is None and constant is None:
        return _MdlDiffuse(module, material.GetPath(),
                           unread=f"{module!r}, with neither {texture_name} nor {constant_name} read")
    return _MdlDiffuse(module, material.GetPath(), texture, constant, tint,
                       names=(texture_name, constant_name, tint_name))


def _first_input(shader, names, convert, context):
    """(name, converted value) of the first of ``names`` the shader authors with a usable value, or
    (None, None)."""
    for name in names:
        value = convert(_input_value(shader, name, context))
        if value is not None:
            return name, value
    return None, None


def _asset_value(value):
    """An authored asset path with a non-empty path, or None."""
    return value if getattr(value, "path", "") else None


def _read_named_mdl_diffuse(shader, material, module, context):
    """The base colour inputs of an MDL module outside ``_MDL_DIFFUSE_INPUTS``, found by the input
    names an Unreal Engine export authors (``_MDL_NAMED_*``), or an ``unread`` entry when it has none.
    The module's own colour expression is not evaluated, so a colour read this way is an
    approximation."""
    texture_name, texture = _first_input(shader, _MDL_NAMED_TEXTURES, _asset_value, context)
    constant_name, constant = _first_input(shader, _MDL_NAMED_CONSTANTS, _colour_value, context)
    tint_name, tint = _first_input(shader, _MDL_NAMED_TINTS, _colour_value, context)
    if texture is None and constant is None:
        return _MdlDiffuse(module, material.GetPath(),
                           unread=f"{module!r}, which authors no colour input the preview reads")
    return _MdlDiffuse(module, material.GetPath(), texture, constant, tint,
                       names=(texture_name, constant_name, tint_name), approximated=True)


def _count_unread(context, material_path, reason):
    """Count a prim drawn without its MDL material's colour under ``reason``; the first material per
    reason is logged, so a scene with hundreds of materials logs a line per module and reason."""
    if reason not in context.mdl_unread:
        logger.info(f"    MDL material {material_path}: {reason}; prims with a material like it take their "
                    f"displayColor or the default colour")
        context.mdl_unread[reason] = 0
    context.mdl_unread[reason] += 1


def _count_approximated(context, diffuse, used):
    """Count a prim coloured by an approximation of its MDL module's colour from the inputs ``used``;
    the first prim per module is logged with its material."""
    if diffuse.module not in context.mdl_approximated:
        logger.info(f"    MDL material {diffuse.material_path}: {diffuse.module!r} coloured from "
                    f"{', '.join(used)}; an approximation, since the module's own colour expression is "
                    f"not evaluated")
        context.mdl_approximated[diffuse.module] = 0
    context.mdl_approximated[diffuse.module] += 1


def _mdl_surface_shader(material, shaders):
    """The shader ``material``'s ``outputs:mdl:surface`` connects to, else its first shader with an
    MDL source asset, or None."""
    from pxr import UsdShade

    try:
        output = material.GetSurfaceOutput("mdl")
        source = output.GetConnectedSource() if output and output.HasConnectedSource() else None
        shader = UsdShade.Shader(source[0].GetPrim()) if source and source[0] else None
        if shader and _mdl_source_asset(shader):
            return shader
    except Exception as e:
        logger.debug(f"    MDL surface output of {material.GetPath()} not read: {e}")
    return next((shader for shader in shaders if _mdl_source_asset(shader)), None)


def _mdl_source_asset(shader):
    """The file name an MDL shader's ``info:mdl:sourceAsset`` authors, or ''."""
    try:
        source = shader.GetSourceAsset("mdl")
    except Exception:
        return ""
    return getattr(source, "path", "") or ""


def _input_value(shader, name, context):
    """The value of a shader input, or None. Its connections are followed one at a time through the
    material's interface inputs and node graph outputs, at most MAX_CONNECTION_HOPS of them; an input
    connected to another shader's output has no value here. The value is that of the last input in the
    chain that authors one, as ``UsdShadeInput.GetValueProducingAttributes`` reads it, which follows the
    chain recursively and without a bound. A chain that loops or runs past the bound is not read
    (``_connections_not_followed``)."""
    from pxr import UsdShade

    shader_input = shader.GetInput(name)
    if not shader_input:
        return None
    chain = [shader_input]
    seen = {shader_input.GetAttr().GetPath()}
    while True:
        sources = chain[-1].GetConnectedSources()[0]
        if not sources:
            break
        source = sources[0]
        if source.sourceType == UsdShade.AttributeType.Output:
            if not source.source.IsContainer():
                return None
            following = source.source.GetOutput(source.sourceName)
        elif source.source.IsContainer():
            following = source.source.GetInput(source.sourceName)
        else:
            break
        if not following:
            break
        path = following.GetAttr().GetPath()
        if path in seen:
            return _connections_not_followed(context, shader_input, "its connections loop")
        if len(chain) > MAX_CONNECTION_HOPS:
            return _connections_not_followed(
                context, shader_input, f"its connections run past {MAX_CONNECTION_HOPS} hops")
        seen.add(path)
        chain.append(following)
    for link in reversed(chain):
        attribute = link.GetAttr()
        if attribute.HasAuthoredValue():
            return attribute.Get() if UsdShade.Input.IsInput(attribute) else None
    return None


def _connections_not_followed(context, shader_input, reason):
    """None, for a shader input whose connections are not followed for ``reason``; the first input per
    reason in a stage is logged."""
    if reason not in context.connections_not_followed:
        context.connections_not_followed.add(reason)
        logger.warning(f"    Shader input {shader_input.GetAttr().GetPath()}: {reason}, so it is not read; "
                       f"inputs like it in this stage are not logged")
    return None


def _colour_value(value):
    """An authored colour as a float (3,) array, or None."""
    if value is None:
        return None
    try:
        rgb = np.asarray(value, dtype=np.float64).reshape(-1)
    except (TypeError, ValueError):
        return None
    return rgb[:3] if len(rgb) >= 3 and np.all(np.isfinite(rgb[:3])) else None


def _asset_path_text(asset_path):
    return getattr(asset_path, "path", None) or str(asset_path).strip("@")


def _load_texture(asset_path, context):
    """The texture named by an SdfAssetPath as an (H, W, 3) uint8 RGB array at most MAX_TEXTURE_SIDE
    pixels on its long side, read only from inside the asset root, or None. Each texture is decoded
    once per stage and every prim that samples it reads the same array. The arrays held count against
    the stage's texture budget (``texture_cache_limit``); a texture that would pass it is not read."""
    authored = getattr(asset_path, "path", "") or ""
    resolved = getattr(asset_path, "resolvedPath", "") or ""
    key = resolved or authored
    if key in context.textures:
        return context.textures[key]
    texels = None
    if context.texture_bytes >= context.texture_budget:
        _over_texture_budget(context, authored or resolved)
    else:
        texels = _read_texture(authored, resolved, context)
        if texels is None:
            logger.warning(f"    Could not load texture: {authored or resolved}")
        elif context.texture_bytes + texels.nbytes > context.texture_budget:
            texels = None
            _over_texture_budget(context, authored or resolved)
        else:
            context.texture_bytes += texels.nbytes
    context.textures[key] = texels
    return texels


def _over_texture_budget(context, name):
    """Count a texture not read because the stage's decoded textures would pass their budget; the first
    is logged."""
    if not context.textures_over_budget:
        logger.warning(f"    Texture {name} not read: the stage's decoded textures would pass the "
                       f"{context.texture_budget / 1024 ** 2:.1f} MiB they may hold; a prim that samples a "
                       f"texture not read takes its material's constant colour or the default")
    context.textures_over_budget += 1


def _read_texture(authored, resolved, context):
    """The texture an asset path names, decoded (``_decode_texture``): from inside the input's USDZ
    archive; else from the path USD resolved or the authored relative path, only inside the asset
    root; else from inside a USDZ archive in the asset root that the resolved path names. None when it
    cannot be read."""
    if os.path.splitext(context.file_path)[1].lower() == ".usdz":
        return _load_texture_from_usdz(context.file_path, authored or resolved)
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
            return _decode_texture(real)
        except Exception as e:
            logger.warning(f"    Could not read texture {authored}: {e}")
    if "[" in resolved:
        package, inner = resolved.split("[", 1)
        real = os.path.realpath(package)
        if real.startswith(context.asset_root + os.sep):
            return _load_texture_from_usdz(real, inner.rstrip("]"))
    return None


def _decode_texture(source):
    """An image (a file path or a binary file object) as an (H, W, 3) uint8 RGB array at most
    MAX_TEXTURE_SIDE pixels on its long side; alpha is dropped. A JPEG is decoded at a reduced scale
    (``Image.draft``); another format is decoded in full and reduced, and only the reduced array is
    kept."""
    with Image.open(source) as image:
        image.draft(None, (MAX_TEXTURE_SIDE, MAX_TEXTURE_SIDE))
        image.load()
        rgb = image if image.mode == "RGB" else image.convert("RGB")
        rgb.thumbnail((MAX_TEXTURE_SIDE, MAX_TEXTURE_SIDE), Image.Resampling.BOX)
        return np.asarray(rgb)


def _load_texture_from_usdz(usdz_path, texture_rel_path):
    """
    Load a texture image from inside a USDZ archive, decoded (``_decode_texture``), or None.
    USDZ is an uncompressed zip containing a .usdc scene + texture files.
    """
    try:
        from ..utils.archive import check_member

        with zipfile.ZipFile(usdz_path, "r") as zf:
            names = zf.namelist()
            clean_path = texture_rel_path.lstrip("./")

            # Strategy 1: Exact path match
            if clean_path in names:
                check_member(zf.getinfo(clean_path), MAX_USDZ_TEXTURE_BYTES)
                with zf.open(clean_path) as f:
                    return _decode_texture(io.BytesIO(f.read()))

            # Strategy 2: Basename match (handles path prefix differences)
            basename = os.path.basename(clean_path)
            for name in names:
                if os.path.basename(name) == basename:
                    check_member(zf.getinfo(name), MAX_USDZ_TEXTURE_BYTES)
                    with zf.open(name) as f:
                        return _decode_texture(io.BytesIO(f.read()))

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
