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

import functools
import io
import os
import re
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

# A root prim beside the default prim is left out of the preview only when it clearly is a rig: the
# largest root of the stage, at least RIG_SCALE_STRONG times larger than the next largest root, or at
# least RIG_SCALE times larger and holding lights or cameras (a thumbnail rig is a huge environment with
# its own lights and camera). Any other root, however small or large, is drawn as before.
RIG_SCALE = 50.0
RIG_SCALE_STRONG = 1000.0
# A root of this share of the largest root's size or more is judged with it, at the same scale.
RIG_SAME_SCALE_SHARE = 0.1
# Prims the independence check of a left-out root visits at most; past it the root counts as used.
MAX_DEPENDENCY_PRIMS = 200_000

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


def load(file_path: str, asset_root: str = None, report: dict = None) -> pv.PolyData:
    """
    Load a USD file and return a PyVista PolyData object for rendering.
    Supports: .usd, .usda, .usdc, .usdz

    ``asset_root`` is the local directory the input's referenced files were fetched into; texture
    files are read only from inside it (default: the input file's directory). ``report``, when given, is
    filled with ``leftOut``: the names of the root prims that look like a rig and were not drawn.
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

    # The default prim is the stage's content (what another file gets when it references this one). A
    # root prim beside it that clearly is a rig (a huge environment with its own lights and camera) is
    # not drawn; every other root prim is, as it is for a stage without a default prim.
    parts, part_roots = _collect_scene(stage.GetPseudoRoot(), xform_cache, context)
    default_prim = _default_prim(stage)
    if default_prim is not None and parts:
        rigs = _find_rigs(stage, default_prim, parts, part_roots)
        if rigs:
            parts = [part for part, root in zip(parts, part_roots) if root not in rigs]
            if report is not None:
                report["leftOut"] = sorted(rigs)
            logger.info("Leaving out the root prims that look like a thumbnail rig: "
                        + "; ".join(f"/{name} ({why})" for name, why in sorted(rigs.items())))
    elif default_prim is not None:
        logger.info(f"The default prim {default_prim.GetPath()} draws no geometry; drawing the whole stage")

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


def _default_prim(stage):
    """The stage's default prim when it is a defined, active root prim, else None."""
    prim = stage.GetDefaultPrim()
    if not prim or not prim.IsActive() or not prim.IsDefined():
        return None
    parent = prim.GetParent()
    if not parent or not parent.IsPseudoRoot():
        return None
    return prim


def _root_name(prim):
    """The name of the root prim that ``prim`` is at or under."""
    return prim.GetPath().GetPrefixes()[0].name


def _collect_parts(root, xform_cache, context, plan=None):
    """(parts, roots): the (points, triangles, colours) parts of the gprims at and under ``root`` in
    traversal order, and the name of the root prim each part came from.

    With a ``plan`` (``_sample_plan``) only the gprims and instances it keeps are read; without one the
    walk stops once the stage passes MAX_POINTS vertices and ``context.capped`` is set."""
    from pxr import Usd, UsdGeom

    time = context.time
    parts = []
    roots = []
    gprim_index = -1
    iterator = iter(Usd.PrimRange(root, Usd.TraverseInstanceProxies()))
    for prim in iterator:
        if prim.IsPseudoRoot():
            continue
        if not _is_rendered(prim, time):
            iterator.PruneChildren()
            continue
        if prim.IsA(UsdGeom.PointInstancer):
            expanded = _expand_point_instancer(prim, xform_cache, context, plan)
            parts.extend(expanded)
            roots.extend([_root_name(prim)] * len(expanded))
            iterator.PruneChildren()
            if plan is None and context.capped:
                break
            continue
        if plan is not None:
            if not prim.IsA(UsdGeom.Gprim):
                continue
            gprim_index += 1
            if not plan.keeps_gprim(gprim_index):
                continue
        part = _gprim_part(prim, np.array(xform_cache.GetLocalToWorldTransform(prim)), context)
        if part is not None:
            parts.append(part)
            roots.append(_root_name(prim))
        if context.points_total > MAX_POINTS:
            context.capped = True
            if plan is not None:
                logger.warning(f"The sampled preview still passes {MAX_POINTS} vertices; "
                               f"remaining prims are skipped")
            break
    return parts, roots


def _collect_scene(root, xform_cache, context):
    """``_collect_parts`` of ``root``; when the stage passes MAX_POINTS vertices, the parts of a
    deterministic sample of its gprims and instances instead, so the preview covers the whole scene
    rather than the first prims in traversal order."""
    parts, roots = _collect_parts(root, xform_cache, context)
    if not context.capped:
        return parts, roots
    del parts, roots
    context.reset_counts()
    plan = _sample_plan(root, xform_cache, context)
    context.reset_counts()
    parts, roots = _collect_parts(root, xform_cache, context, plan)
    logger.warning(f"Stage expands past {MAX_POINTS} vertices (about {plan.total_vertices:,} in "
                   f"{plan.total_items:,} gprims and instances); the preview shows {plan.kept_items:,} of "
                   f"them: every small one, the outermost ones and an evenly spread "
                   f"{plan.fraction:.0%} of the rest, so it covers the whole scene")
    return parts, roots


# Share of MAX_POINTS the sampled preview aims at; the rest absorbs the outermost items kept beside
# the sample and the estimate's rounding.
SAMPLE_BUDGET_SHARE = 0.9
# An item whose vertex count is at most this share of the stage's average per item is always kept: a
# small, distinct object costs the budget next to nothing and the sample must not drop it.
LIGHT_ITEM_SHARE = 0.25
_GOLDEN = 0.6180339887498949


def _spread(count, fraction):
    """A boolean array of ``count`` flags, about ``fraction`` of them set, spread evenly and
    deterministically along the index (the fractional part of index * golden ratio, which has no
    period, so a regular grid keeps no rows or columns)."""
    return (np.arange(count, dtype=np.float64) * _GOLDEN) % 1.0 < fraction


class _SamplePlan:
    """The gprims (by traversal index) and point-instancer instances a sampled load reads."""

    def __init__(self, fraction, gprim_keep, instancer_keep, total_vertices, total_items):
        self.fraction = fraction
        self.gprim_keep = gprim_keep
        self.instancer_keep = instancer_keep
        self.total_vertices = total_vertices
        self.total_items = total_items
        self.kept_items = int(gprim_keep.sum()) + sum(int(mask.sum()) for mask in instancer_keep.values())

    def keeps_gprim(self, index):
        return index >= len(self.gprim_keep) or bool(self.gprim_keep[index])

    def instance_mask(self, path, count):
        mask = self.instancer_keep.get(str(path))
        if mask is None or len(mask) != count:
            return np.ones(count, dtype=bool)
        return mask


def _local_extent(prim, cache, context):
    """(vertex count, local min, local max) of one gprim, from its real tessellation, once per
    prototype prim or implicit shape."""
    from pxr import UsdGeom

    key = prim.GetPrimInPrototype().GetPath() if prim.IsInstanceProxy() else prim.GetPath()
    if key in cache:
        return cache[key]
    if prim.IsA(UsdGeom.Mesh):
        points = UsdGeom.Mesh(prim).GetPointsAttr().Get(context.time)
        if points is None or len(points) == 0:
            result = (0, None, None)
        else:
            array = np.asarray(points, dtype=np.float64).reshape(-1, 3)
            result = (len(array), array.min(axis=0), array.max(axis=0))
    else:
        shape_key = _implicit_key(prim, context.time)
        if shape_key is None:
            result = (0, None, None)
        else:
            shape = _implicit_shape(shape_key)
            bounds = np.asarray(shape.bounds, dtype=np.float64).reshape(3, 2)
            result = (int(shape.n_points), bounds[:, 0], bounds[:, 1])
    cache[key] = result
    return result


def _world_box(lo, hi, matrix):
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    world = _transform(corners, matrix)
    return world.min(axis=0), world.max(axis=0)


def _sample_plan(root, xform_cache, context):
    """Count the gprims and instances under ``root`` without tessellating them and choose what to read:
    every item whose cost is small, the items holding the scene's outermost bounds, and an evenly
    spread fraction of the rest chosen so the sample stays within the budget."""
    from pxr import Usd, UsdGeom

    time = context.time
    cache = {}
    gprim_costs = []
    low_pick = [(np.inf, -1)] * 3     # per axis: lowest world minimum and the gprim holding it
    high_pick = [(-np.inf, -1)] * 3
    instancers = []                   # (path, per-instance vertex cost, world translations)
    gprim_index = -1
    iterator = iter(Usd.PrimRange(root, Usd.TraverseInstanceProxies()))
    for prim in iterator:
        if prim.IsPseudoRoot():
            continue
        if not _is_rendered(prim, time):
            iterator.PruneChildren()
            continue
        if prim.IsA(UsdGeom.PointInstancer):
            iterator.PruneChildren()
            entry = _count_instancer(prim, xform_cache, context, cache)
            if entry is not None:
                instancers.append(entry)
            continue
        if not prim.IsA(UsdGeom.Gprim):
            continue
        gprim_index += 1
        count, lo, hi = _local_extent(prim, cache, context)
        gprim_costs.append(count)
        if lo is not None:
            matrix = np.array(xform_cache.GetLocalToWorldTransform(prim))
            wlo, whi = _world_box(lo, hi, matrix)
            for axis in range(3):
                if wlo[axis] < low_pick[axis][0]:
                    low_pick[axis] = (wlo[axis], gprim_index)
                if whi[axis] > high_pick[axis][0]:
                    high_pick[axis] = (whi[axis], gprim_index)

    gprim_costs = np.asarray(gprim_costs, dtype=np.int64)
    total_items = len(gprim_costs) + sum(len(entry[1]) for entry in instancers)
    total_vertices = int(gprim_costs.sum()) + sum(int(entry[1].sum()) for entry in instancers)
    average = total_vertices / max(total_items, 1)
    threshold = average * LIGHT_ITEM_SHARE

    light_vertices = int(gprim_costs[gprim_costs <= threshold].sum()) + sum(
        int(entry[1][entry[1] <= threshold].sum()) for entry in instancers)
    heavy_vertices = total_vertices - light_vertices
    budget = MAX_POINTS * SAMPLE_BUDGET_SHARE
    if heavy_vertices <= 0:
        fraction = 1.0
    else:
        fraction = float(min(1.0, max(0.02, (budget - light_vertices) / heavy_vertices)))

    gprim_keep = (gprim_costs <= threshold) | _spread(len(gprim_costs), fraction)
    for axis in range(3):
        for index in (low_pick[axis][1], high_pick[axis][1]):
            if index >= 0:
                gprim_keep[index] = True
    instancer_keep = {}
    for path, costs, translations in instancers:
        keep = (costs <= threshold) | _spread(len(costs), fraction)
        for axis in range(3):
            keep[int(translations[:, axis].argmin())] = True
            keep[int(translations[:, axis].argmax())] = True
        instancer_keep[str(path)] = keep
    return _SamplePlan(fraction, gprim_keep, instancer_keep, total_vertices, total_items)


def _count_instancer(prim, xform_cache, context, cache):
    """(path, vertex cost of each instance, world translations of the instances) of a PointInstancer,
    or None."""
    from pxr import Usd, UsdGeom

    instancer = UsdGeom.PointInstancer(prim)
    time = context.time
    transforms = instancer.ComputeInstanceTransformsAtTime(time, time)
    proto_indices = instancer.GetProtoIndicesAttr().Get(time)
    targets = instancer.GetPrototypesRel().GetTargets()
    if not transforms or proto_indices is None or not targets:
        return None
    stage = prim.GetStage()
    per_prototype = []
    for target in targets:
        root = stage.GetPrimAtPath(target)
        vertices = 0
        if root:
            for child in Usd.PrimRange(root, Usd.TraverseInstanceProxies()):
                if _is_rendered(child, time) and child.IsA(UsdGeom.Gprim):
                    vertices += _local_extent(child, cache, context)[0]
        per_prototype.append(vertices)
    indices = np.asarray(proto_indices, dtype=np.int64)
    valid = (indices >= 0) & (indices < len(per_prototype))
    costs = np.zeros(len(indices), dtype=np.int64)
    costs[valid] = np.asarray(per_prototype, dtype=np.int64)[indices[valid]]
    instancer_to_world = np.array(xform_cache.GetLocalToWorldTransform(prim))
    world = np.array(transforms, dtype=np.float64).reshape(-1, 4, 4) @ instancer_to_world
    if len(world) != len(costs):
        return None
    return prim.GetPath(), costs, world[:, 3, :3]


# --------------------------------------------------------------------------------------------------
# Root prims beside the default prim
# --------------------------------------------------------------------------------------------------

def _holds_rig_content(stage, name):
    """True when the root prim ``name`` holds a light or a camera."""
    from pxr import Usd, UsdGeom

    root = stage.GetPrimAtPath("/" + name)
    for prim in Usd.PrimRange(root, Usd.TraverseInstanceProxies()):
        if prim.IsA(UsdGeom.Camera) or str(prim.GetTypeName()).endswith("Light"):
            return True
    return False


def _find_rigs(stage, default_prim, parts, part_roots):
    """The root prims beside the default prim that clearly are a rig, as {name: why}.

    Each root's size is the largest side of the bounds of what it draws. The largest root, when it is
    not the default prim, is a rig if it is at least RIG_SCALE_STRONG times larger than every root
    below its scale class, or at least RIG_SCALE times larger and holds lights or cameras; roots of
    its own scale class (at least RIG_SAME_SCALE_SHARE of its size) are judged with it. Nothing else is
    left out: a placeholder default prim, peer roots and a site plan beside a building are all drawn.
    """
    bounds = {}
    for (points, _, _), root in zip(parts, part_roots):
        low, high = points.min(axis=0), points.max(axis=0)
        if root in bounds:
            low, high = np.minimum(low, bounds[root][0]), np.maximum(high, bounds[root][1])
        bounds[root] = (low, high)
    sizes = {root: float((high - low).max()) for root, (low, high) in bounds.items()}
    default_name = default_prim.GetName()
    if len(sizes) < 2 or default_name not in sizes:
        return {}
    largest = max(sizes.values())
    if sizes[default_name] >= largest * RIG_SAME_SCALE_SHARE:
        return {}
    top_class = [name for name, size in sizes.items() if size >= largest * RIG_SAME_SCALE_SHARE]
    below = [size for name, size in sizes.items() if name not in top_class]
    reference = max(below) if below else 0.0
    if reference <= 0.0:
        return {}
    ratio = min(sizes[name] for name in top_class) / reference
    if ratio < RIG_SCALE:
        return {}
    rigs = {}
    for name in top_class:
        if ratio >= RIG_SCALE_STRONG:
            rigs[name] = f"{sizes[name]:.4g} units across, {ratio:,.0f} times the largest other root"
        elif _holds_rig_content(stage, name):
            rigs[name] = (f"{sizes[name]:.4g} units across, {ratio:,.0f} times the largest other root, "
                          f"holds lights or cameras")
    return rigs


def left_out_roots_unused(file_path, left_out):
    """The names in ``left_out`` that nothing in the rest of the stage depends on, so the files only
    they name add nothing to the preview. A root counts as used when any prim outside the left-out
    roots has a composition arc (internal reference, payload, inherit, specialize), a relationship
    target or an attribute connection into it, or when that cannot be established."""
    from pxr import Sdf, Usd

    try:
        stage = Usd.Stage.Open(file_path)
        if stage is None:
            return []
        rig_paths = {name: Sdf.Path("/" + name) for name in left_out}
        used = set()

        def into(path):
            for name, rig_path in rig_paths.items():
                if path.HasPrefix(rig_path):
                    used.add(name)

        visited = 0
        for child in stage.GetPseudoRoot().GetChildren():
            if child.GetName() in rig_paths:
                continue
            predicate = Usd.TraverseInstanceProxies(Usd.PrimAllPrimsPredicate)
            for prim in Usd.PrimRange(child, predicate):
                visited += 1
                if visited > MAX_DEPENDENCY_PRIMS:
                    return []
                for spec in prim.GetPrimStack():
                    for arcs in (spec.referenceList, spec.payloadList):
                        for arc in arcs.GetAddedOrExplicitItems():
                            if arc.primPath and not arc.assetPath:
                                into(arc.primPath)
                    for arcs in (spec.inheritPathList, spec.specializesList):
                        for path in arcs.GetAddedOrExplicitItems():
                            into(path)
                for relationship in prim.GetAuthoredRelationships():
                    for path in relationship.GetTargets():
                        into(path)
                for attribute in prim.GetAuthoredAttributes():
                    if attribute.HasAuthoredConnections():
                        for path in attribute.GetConnections():
                            into(path)
        return [name for name in left_out if name not in used]
    except Exception as e:
        logger.info(f"Could not check what depends on the left-out root prims ({type(e).__name__}); "
                    f"every skipped file stays reported")
        return []


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
        # Set when a walk stopped at MAX_POINTS vertices; ``_collect_scene`` then samples the stage.
        self.capped = False

    def reset_counts(self):
        """Forget what a discarded walk counted, so a second walk of the stage starts clean."""
        self.points_total = 0
        self.capped = False
        self.mdl_unread.clear()
        self.mdl_approximated.clear()


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


def _implicit_key(prim, time):
    """The kind and parameters that decide an implicit gprim's tessellation, or None for another prim."""
    from pxr import UsdGeom

    if prim.IsA(UsdGeom.Cube):
        return ("Cube", float(UsdGeom.Cube(prim).GetSizeAttr().Get(time) or 2.0))
    if prim.IsA(UsdGeom.Sphere):
        return ("Sphere", float(UsdGeom.Sphere(prim).GetRadiusAttr().Get(time) or 1.0))
    for kind, schema_type in (("Cylinder", UsdGeom.Cylinder), ("Cone", UsdGeom.Cone),
                              ("Capsule", UsdGeom.Capsule)):
        if prim.IsA(schema_type):
            schema = schema_type(prim)
            return (kind, float(schema.GetRadiusAttr().Get(time) or 1.0),
                    float(schema.GetHeightAttr().Get(time) or 2.0),
                    str(schema.GetAxisAttr().Get(time) or "Z"))
    return None


@functools.lru_cache(maxsize=512)
def _implicit_shape(key):
    """The triangulated, cleaned tessellation of an implicit shape in its local space. Shared between
    prims of the same kind and size: callers must not change it."""
    kind = key[0]
    if kind == "Cube":
        size = key[1]
        shape = pv.Cube(x_length=size, y_length=size, z_length=size)
    elif kind == "Sphere":
        shape = pv.Sphere(radius=key[1], theta_resolution=_IMPLICIT_RESOLUTION,
                          phi_resolution=_IMPLICIT_RESOLUTION // 2)
    else:
        _, radius, height, axis = key
        direction = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0)}.get(axis, (0.0, 0.0, 1.0))
        if kind == "Cone":
            shape = pv.Cone(direction=direction, height=height, radius=radius,
                            resolution=_IMPLICIT_RESOLUTION)
        else:
            shape = pv.Cylinder(direction=direction, radius=radius, height=height,
                                resolution=_IMPLICIT_RESOLUTION, capping=True)
            if kind == "Capsule":
                offset = np.asarray(direction) * height / 2.0
                for sign in (1.0, -1.0):
                    shape = shape.merge(pv.Sphere(radius=radius, center=tuple(sign * offset),
                                                  theta_resolution=_IMPLICIT_RESOLUTION,
                                                  phi_resolution=_IMPLICIT_RESOLUTION // 2))
    return shape.triangulate().clean()


def _implicit_geometry(prim, context):
    """Tessellate a Cube, Sphere, Cylinder, Cone or Capsule in its local space."""
    time = context.time
    key = _implicit_key(prim, time)
    if key is None:
        return None
    shape = _implicit_shape(key)
    points = np.asarray(shape.points, dtype=np.float64)
    triangles = np.asarray(shape.faces).reshape(-1, 4)[:, 1:].astype(np.int64)
    colours = _material_base_colour(prim, len(points))
    if colours is None:
        colours = _mdl_colours(prim, None, context, len(points))
    if colours is None:
        colours = _constant_primvar_colour(prim, "displayColor", len(points), time)
    logger.info(f"  Prim {prim.GetPath()}: implicit {prim.GetTypeName()}, {len(points)} vertices")
    return points, triangles, colours


def _expand_point_instancer(prim, xform_cache, context, plan=None):
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
    keep = plan.instance_mask(prim.GetPath(), len(proto_indices)) if plan is not None else None
    for number, (instance, matrix) in enumerate(zip(proto_indices, transforms)):
        if keep is not None and not keep[number]:
            continue
        if instance < 0 or instance >= len(prototypes):
            continue
        world = np.array(matrix) @ instancer_to_world
        for points, triangles, colours in prototypes[instance]:
            if context.points_total > MAX_POINTS:
                context.capped = True
                if plan is not None:
                    logger.warning(f"Point instancer {prim.GetPath()}: the sampled instances still pass "
                                   f"the vertex limit, remaining instances skipped")
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
# A role-less two-component primvar is taken as a UV set only under a name that reads as one.
_UV_SET_NAME = re.compile(r"^(st|uv|uvmap|map|texcoord)_?\d+$", re.IGNORECASE)
_UV_SET_SCALAR_TYPES = ("float2", "double2", "half2")


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
    """Names of the prim's UV-set array primvars that ``named`` does not list, sorted: those typed as
    texture coordinates (``texCoord2f[]``), and role-less two-component ones (``float2[]``) whose name
    reads as a UV set (``st_0``, ``uv1``, ``map1``), as some exporters write them."""
    from pxr import UsdGeom

    found = []
    for primvar in UsdGeom.PrimvarsAPI(prim).GetPrimvarsWithValues():
        type_name = primvar.GetTypeName()
        name = str(primvar.GetPrimvarName())
        if not type_name.isArray or name in named:
            continue
        if type_name.role == "TextureCoordinate" or (
                not type_name.role and str(type_name.scalarType) in _UV_SET_SCALAR_TYPES
                and _UV_SET_NAME.match(name)):
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
