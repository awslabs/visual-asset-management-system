"""
Mesh export and S3 upload.

Writes the requested formats into a local output directory, then uploads that directory to the
output-files prefix preserving its internal structure -- an OBJ's MTL and its textures must stay in
the same relative arrangement or the material's ``map_Kd`` no longer resolves.

Each format's exporter emits whatever texture the mesh's own visual carries at the point it is
called: an OBJ gets its MTL and maps written alongside, a GLB gets them embedded, an STL carries no
colour by design. What the visual carries after the cleanup stage is decided in ``texturing``, whose
degraded path copies the model's raw texture files in through ``copy_texture_sidecars``.

Output keys follow the relative-subdirectory rule: an input file two folders deep inside an asset
produces its outputs two folders deep under the output prefix, so the write-back step can map them
onto the asset. No per-job folder is created here -- the workflow's
``defaultOutputFileBaseExecutionPathExtension`` is what separates runs, and a container-side folder
would appear as a stray level inside every asset.
"""

import logging
import os
import shutil
from typing import Optional

logger = logging.getLogger(__name__)

EXPORT_FORMATS = ("glb", "obj", "stl")

# Containers that declare Y as the up axis and therefore need the mesh rotated out of the Z-up space
# the models reconstruct into. OBJ and STL carry no up-axis declaration and are written as they are.
GLTF_FORMATS = ("glb", "gltf")

# Where the model's raw texture files land when they ship on their own. Kept out of the output root
# so they cannot be overwritten by the OBJ exporter's own ``material.mtl`` / ``material_0.png``.
SIDECAR_SUBDIR = "textures"

# The reserved name the workflow's output processing reads as asset-level metadata. Every other
# ``*.metadata.json`` name is read as file-level metadata for the file its stem names.
ASSET_METADATA_FILENAME = "asset.metadata.json"
# The suffix the write-back reads a metadata document by, whatever it attaches to.
METADATA_SUFFIX = ".metadata.json"

# Formats that can carry colour in the file itself. STL stores triangles and nothing else, so a run
# whose mesh is textured still writes a colourless STL -- which is the format, not a fault, and is worth
# stating on the file rather than leaving a viewer to discover.
COLOUR_CARRYING_FORMATS = ("glb", "gltf", "obj")


class ExportError(RuntimeError):
    """An export or upload that cannot be completed."""


def parse_export_formats(raw) -> list:
    """The formats to write, de-duplicated and in a stable order.

    An empty value means the default set: an optional enum/string tag can submit "" from a headless
    trigger, and "" must not mean "write nothing".
    """
    if isinstance(raw, (list, tuple)):
        names = [str(n).strip().lower() for n in raw if str(n).strip()]
    else:
        names = [n.strip().lower() for n in str(raw or "").split(",") if n.strip()]
    if not names:
        names = list(EXPORT_FORMATS)

    unknown = [n for n in names if n not in EXPORT_FORMATS]
    if unknown:
        raise ExportError(
            f"Unknown export format(s) {', '.join(unknown)}; "
            f"expected a comma list drawn from {', '.join(EXPORT_FORMATS)}")
    return [fmt for fmt in EXPORT_FORMATS if fmt in set(names)]


def _relative_within_pipeline_output(parts):
    """The path between a pipeline output's ``files/`` marker and its filename, or ``None``.

    VAMS writes each pipeline step's file outputs under
    ``pipelines/{pipelineId}/{jobName}/output/{executionId}/files/{relative}/{name}``, and a chained
    workflow step is handed that key as its input. The segments after ``.../output/{executionId}/files``
    (the filename excluded) are the file's path within the asset -- so a nested input still lands at
    the same depth and one at the prior step's root has no subdir. Returns ``None`` when the key does
    not carry that ``output/<id>/files`` structure, so a key that is neither an asset key nor a
    pipeline output is rejected by the caller rather than silently relocated.
    """
    for i in range(len(parts) - 2):
        if parts[i] == "output" and parts[i + 2] == "files":
            return parts[i + 3:-1]
    return None


def compute_relative_subdir(object_key: str, asset_id: str) -> str:
    """The input file's folder path within its asset, with a trailing slash when non-empty.

    ``assetId`` is threaded through the pipeline definition, never derived from the key: an asset
    whose id happens to match a folder name elsewhere in the key would otherwise silently relocate
    every output. An absent id is an error rather than a fallback to the asset root, since writing
    to the wrong prefix looks like success -- except for a chained workflow step, whose input is the
    PRIOR step's output and so carries no assetId at all; there the path within the asset is read
    from the pipeline output convention instead (see ``_relative_within_pipeline_output``).
    """
    if not asset_id:
        raise ExportError(
            "assetId is required to compose output keys; without it the relative path within the "
            "asset cannot be determined")
    if not object_key:
        raise ExportError("An input objectKey is required to compose output keys")

    parts = str(object_key).split("/")
    if asset_id in parts:
        index = parts.index(asset_id)
        subdir_parts = parts[index + 1:-1]
        return "/".join(subdir_parts) + "/" if subdir_parts else ""

    # assetId absent: the only legitimate case is a chained step reading a prior step's output.
    subdir_parts = _relative_within_pipeline_output(parts)
    if subdir_parts is None:
        raise ExportError(
            f"assetId '{asset_id}' does not appear in the input key '{object_key}', and it is not a "
            f"pipeline output key; the relative path within the asset cannot be determined")
    return "/".join(subdir_parts) + "/" if subdir_parts else ""


def compose_output_key(output_dir: str, relative_subdir: str, relative_path: str) -> str:
    """``{output_base}{relative_subdir}{relative_path}`` with ``/`` separators throughout."""
    base = str(output_dir or "")
    if base and not base.endswith("/"):
        base += "/"
    relative = str(relative_path or "").replace("\\", "/").lstrip("/")
    return f"{base}{relative_subdir}{relative}"


def decimate(mesh, target_face_count: int):
    """Reduce the mesh to a face budget. 0 or a budget above the current count is a no-op."""
    try:
        target = int(target_face_count or 0)
    except (TypeError, ValueError):
        logger.warning(f"Ignoring an unreadable TARGET_FACE_COUNT '{target_face_count}'")
        return mesh
    if target <= 0:
        logger.info("Decimation disabled (TARGET_FACE_COUNT is 0)")
        return mesh

    current = len(mesh.faces)
    if current <= target:
        logger.info(f"Mesh has {current} faces, at or below the {target} face budget; not decimated")
        return mesh

    logger.info(f"Decimating from {current} to {target} faces")
    mesh = _welded_for_decimation(mesh)
    # face_count is passed by NAME: the first positional parameter of
    # trimesh's wrapper is ``percent``, a reduction ratio in [0, 1], so a face
    # budget supplied positionally is rejected outright.
    simplified = mesh.simplify_quadric_decimation(face_count=target)
    logger.info(f"Decimated to {len(simplified.faces)} faces")
    return simplified


def _welded_for_decimation(mesh):
    """The mesh with vertices merged across uv and normal seams, ready to be reduced.

    A textured mesh carries a separate vertex per face corner wherever its atlas has a seam. The
    decimator treats those as distinct points, so collapsing an edge that crosses a seam leaves
    junctions the surface no longer closes over -- measured on a seam-split sphere, reducing by as
    little as a tenth produced 127 non-manifold edges and 124 broken faces, while the same sphere
    welded reduced cleanly to a quarter of its faces with none.

    Welding costs nothing here: the decimator returns geometry alone, discarding the uv field
    whatever it is given, and the texture is re-projected onto the reduced topology afterwards.
    Only a mesh that is actually being reduced passes through this, so a mesh under its face budget
    keeps its own vertex set and its texture stays attached rather than re-projected.
    """
    try:
        welded = mesh.copy()
        welded.merge_vertices(merge_tex=True, merge_norm=True)
    except Exception as e:
        logger.warning(f"Could not weld the mesh before decimating it: {e}. Reducing the split "
                       f"vertex set instead, which can leave non-manifold junctions behind")
        return mesh

    before, after = len(mesh.vertices), len(welded.vertices)
    if after < before:
        logger.info(f"Welded {before} vertices to {after} across uv and normal seams before "
                    f"decimating, so the reduction cannot open the surface at a seam")
    return welded


def rescale(mesh, target_size_mm: float):
    """Scale the mesh so its longest bounding-box edge is ``target_size_mm``. 0 is a no-op."""
    try:
        target = float(target_size_mm or 0)
    except (TypeError, ValueError):
        logger.warning(f"Ignoring an unreadable TARGET_SIZE_MM '{target_size_mm}'")
        return mesh
    if target <= 0:
        logger.info("Rescaling disabled (TARGET_SIZE_MM is 0)")
        return mesh

    extents = [float(e) for e in mesh.extents]
    longest = max(extents) if extents else 0.0
    if longest <= 0:
        logger.warning("The mesh has no measurable extent; not rescaled")
        return mesh

    factor = target / longest
    logger.info(f"Rescaling by {factor:.6f} so the longest edge is {target} mm")
    mesh.apply_scale(factor)
    return mesh


# The axes a model may treat as up. Everything downstream of generation works in Z-up.
def shape_descriptors(mesh) -> dict:
    """Coarse shape measurements of the finished mesh, for comparing models on the same subject.

    None of these is a verdict on its own -- a legitimately solid subject fills its hull, and a
    rotationally symmetric one has a round footprint. They earn their keep by being compared ACROSS
    models reconstructing the SAME photograph, where the models should agree and a disagreement points
    at whichever one is wrong. Measured on four models given one mug:

        model         fill   footprint aspect   height/width
        stable3dgen   0.217        1.224            0.758      faithful, handle present
        sam3d         0.435        1.325            1.334      faithful, handle present
        trellis2      0.436        1.004            0.808      round footprint: no handle
        triposr       0.936        0.964            0.889      round AND filled: a billet

    ``fill`` is the volume against the convex hull's, which separates a hollow vessel from a solid
    lump: TripoSR's was 2.15x the median of its siblings on the same subject. ``footprintAspect`` is
    the ground-plane aspect, which a protruding feature such as a handle pushes away from 1.
    ``heightOverWidth`` is proportion, where the models disagreed by 1.76x on one subject -- they
    cannot all be right.

    ``medianCrossings`` and ``shellLineShare`` are the shell descriptors (see ``shell_descriptors``): how
    many times a lattice of parallel rays crosses the surface. A solid reads 2 on every ray; a second wall
    inside the first reads more than 2 on the rays through it, which genus cannot see -- an inner shell
    fused to the outer wall reads as tunnels, and one clear of it reads as a second closed component.

    Measured on the mesh as it ships, so the numbers describe the artifact rather than an intermediate.
    """
    descriptors = {}
    try:
        extents = [float(e) for e in mesh.extents]
        volume = abs(float(mesh.volume))
        hull = abs(float(mesh.convex_hull.volume))
    except Exception as e:
        logger.warning(f"Could not measure the mesh's shape descriptors: {e}")
        return descriptors

    if hull > 0:
        descriptors["fillRatio"] = round(volume / hull, 4)
    if len(extents) == 3 and extents[1] > 0:
        descriptors["footprintAspect"] = round(extents[0] / extents[1], 4)
    if len(extents) == 3 and min(extents[0], extents[1]) > 0:
        descriptors["heightOverWidth"] = round(extents[2] / min(extents[0], extents[1]), 4)

    genus = surface_genus(mesh)
    if genus is not None:
        descriptors["genus"] = genus
    descriptors.update(shell_descriptors(mesh))
    logger.info(f"Shape descriptors: {descriptors}")
    return descriptors


def surface_genus(mesh) -> Optional[int]:
    """The number of TUNNELS through the mesh, or ``None`` when it is not a closed surface.

    The one thing a run reports that no other measurement can see. Watertight, manifold and single-body are
    all satisfied by a closed surface carrying any number of handles, so a tunnel surfacing as a pinhole
    through a head passes every print-gate criterion with nothing to show for it. Measured across the five
    models on one photograph of a person: SAM 3D 16, TRELLIS.2 7 and 9 at 200,000 and 500,000 face budgets,
    Stable3DGen 5, TripoSR 8. Handles are what single-image reconstruction does, not one model's quirk.

    Reported here rather than as a gate criterion. A tunnel usually prints, so it is not a printability
    failure; what it is, is a defect in a LIKENESS, and this pipeline's comparison across models on one
    subject is where that belongs.

    Measured on a welded copy for the same reason the gate is: a textured mesh carries a separate vertex
    per face corner wherever its atlas has a seam, and over the exported indices that reads as an open
    surface. A re-baked mesh whose genus is 7 reports -891 unwelded.
    """
    try:
        import meshcleanup

        welded = meshcleanup.welded_for_measurement(mesh)
        if not bool(welded.is_watertight):
            logger.info("The mesh is not a closed surface once welded, so its genus is undefined and is "
                        "not reported")
            return None
        # For C closed orientable components the Euler characteristic is 2C - 2g, so the component count
        # has to come out before the genus does -- a run whose subject came apart into two closed shells
        # would otherwise report a tunnel it does not have.
        components = max(int(getattr(welded, "body_count", 1) or 1), 1)
        doubled = 2 * components - int(welded.euler_number)
        if doubled < 0 or doubled % 2:
            logger.warning(f"The welded mesh has Euler characteristic {welded.euler_number} over "
                           f"{components} closed component(s), which is not a genus; not reported")
            return None
        return doubled // 2
    except Exception as e:
        logger.warning(f"Could not measure the mesh's genus ({type(e).__name__}: {e}); not reported")
        return None


# The ray lattice the shell descriptors are measured on: rays per side across the two shorter axes, and
# how far in from the bounding box, as a share of its extent, the outermost rays sit.
SHELL_RAY_LATTICE = 24
SHELL_RAY_INSET = 0.02
# Candidate (triangle, ray) pairs are expanded this many at a time, so a mesh of a few enormous triangles
# cannot ask for a triangles-by-rays array in one piece.
SHELL_RAY_CANDIDATE_BLOCK = 2_000_000


def shell_descriptors(mesh) -> dict:
    """How many times the surface stands between a ray's entry and its exit, over a lattice of rays.

    A closed solid is crossed exactly twice by any ray through it: in once, out once. A second wall inside
    the first adds two crossings on every ray through the void it encloses, so the same lattice reads 4
    where one inner wall stands and 6 where the void's far side is walled too. Genus cannot make this
    distinction -- an inner shell fused to the outer wall reads as tunnels, and one clear of it reads as a
    second closed component -- and the print gate's watertight, manifold and body-count checks pass on
    both. Measured on the recorded SAM 3D outputs: a raw mesh reads a median of 6 with 77-90% of its rays
    crossing more than twice, while a solid reconstruction of the same subject reads a median of 2 with 3%.

    ``medianCrossings`` is the median crossing count over the rays that met the surface at all, and
    ``shellLineShare`` the share of those rays that crossed more than twice. Both are ``None`` when the
    mesh has no faces or no ray met it.
    """
    import numpy as np

    try:
        _, counts = ray_crossings(mesh)
    except Exception as e:
        logger.warning(f"Could not measure the mesh's shell crossings ({type(e).__name__}: {e}); not "
                       f"reported")
        counts = None
    if counts is None or len(counts) == 0:
        return {"medianCrossings": None, "shellLineShare": None}
    return {"medianCrossings": round(float(np.median(counts)), 4),
            "shellLineShare": round(float(np.mean(counts > 2)), 4)}


def ray_crossings(mesh, lattice: int = SHELL_RAY_LATTICE, inset: float = SHELL_RAY_INSET):
    """Surface crossings per ray for a lattice of parallel rays along the mesh's longest bounding-box axis.

    Returns ``(axis, counts)``: the axis the rays run along, and the crossing count of every ray that met
    the surface, in no particular order. ``lattice`` rays per side are spread evenly over the other two
    axes, the outermost ``inset`` of the extent in from the bounding box so no ray grazes the silhouette.
    ``(None, None)`` when the mesh has no faces.

    A ray's crossings are the DISTINCT DEPTHS at which it meets the surface, not the number of triangles:
    a ray through a shared edge or a vertex meets every triangle around it at one depth, and that is one
    crossing. Counting triangles would read an axis-aligned box as 4 along its face diagonals.

    Every triangle is binned by its projected bounding box onto the lattice before the point-in-triangle
    test, so the work is proportional to the triangles' footprint over the lattice rather than to every
    triangle against every ray. Deterministic: there is no sampling.
    """
    import numpy as np

    faces = np.asarray(getattr(mesh, "faces", ()), dtype=np.int64)
    if faces.ndim != 2 or len(faces) == 0:
        return None, None
    vertices = np.asarray(mesh.vertices, dtype=float)
    extents = np.asarray(mesh.extents, dtype=float)
    bounds = np.asarray(mesh.bounds, dtype=float)
    axis = int(np.argmax(extents))
    across = [i for i in range(3) if i != axis]

    n = max(int(lattice), 1)
    starts = bounds[0][across] + inset * extents[across]
    steps = (extents[across] * (1.0 - 2.0 * inset)) / max(n - 1, 1)
    if not np.all(steps > 0):
        # Flat across one lattice axis: every triangle projects to a line, which no ray can pierce.
        return axis, np.zeros(0, dtype=np.int64)

    corners = vertices[faces]                                       # (F, 3, 3)
    depth = corners[:, :, axis]                                     # (F, 3) along the ray
    flat = corners[:, :, across]                                    # (F, 3, 2) across it
    lo_idx = np.maximum(np.ceil((flat.min(axis=1) - starts) / steps - 1e-9), 0).astype(np.int64)
    hi_idx = np.minimum(np.floor((flat.max(axis=1) - starts) / steps + 1e-9), n - 1).astype(np.int64)
    spans = np.maximum(hi_idx - lo_idx + 1, 0)                      # (F, 2) lattice lines each one covers

    cumulative = np.cumsum(spans[:, 0] * spans[:, 1])
    total = int(cumulative[-1])
    edges = np.searchsorted(cumulative, np.arange(0, total, SHELL_RAY_CANDIDATE_BLOCK), side="right")
    edges = np.unique(np.concatenate([[0], edges, [len(faces)]]))
    rays, depths = [], []
    for start, stop in zip(edges[:-1], edges[1:]):
        ray, z = _lattice_hits(flat[start:stop], depth[start:stop], lo_idx[start:stop],
                               spans[start:stop], starts, steps, n)
        rays.append(ray)
        depths.append(z)
    ray = np.concatenate(rays) if rays else np.zeros(0, dtype=np.int64)
    z = np.concatenate(depths) if depths else np.zeros(0, dtype=float)
    if len(ray) == 0:
        return axis, np.zeros(0, dtype=np.int64)

    order = np.lexsort((z, ray))
    ray, z = ray[order], z[order]
    distinct = np.ones(len(ray), dtype=bool)
    distinct[1:] = (ray[1:] != ray[:-1]) | (np.diff(z) > 1e-6 * extents[axis])
    counts = np.bincount(ray[distinct], minlength=n * n)
    return axis, counts[counts > 0]


def _lattice_hits(flat, depth, lo_idx, spans, starts, steps, n):
    """The (ray index, depth) of every lattice ray piercing one of these triangles.

    Each triangle is expanded to the lattice points inside its projected bounding box, and only those
    candidates take the barycentric point-in-triangle test.
    """
    import numpy as np

    per_triangle = spans[:, 0] * spans[:, 1]
    total = int(per_triangle.sum())
    if total == 0:
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=float)
    tri = np.repeat(np.arange(len(flat)), per_triangle)
    offset = np.arange(total) - np.repeat(np.cumsum(per_triangle) - per_triangle, per_triangle)
    i = lo_idx[tri, 0] + offset // spans[tri, 1]
    j = lo_idx[tri, 1] + offset % spans[tri, 1]
    point = starts + np.stack([i, j], axis=1) * steps

    q0, q1, q2 = flat[tri, 0], flat[tri, 1], flat[tri, 2]
    doubled_area = ((q1[:, 1] - q2[:, 1]) * (q0[:, 0] - q2[:, 0])
                    + (q2[:, 0] - q1[:, 0]) * (q0[:, 1] - q2[:, 1]))
    # A triangle edge-on to the ray projects to a line; it is not pierced and would divide by zero.
    pierceable = np.abs(doubled_area) > 1e-12 * steps[0] * steps[1]
    safe = np.where(pierceable, doubled_area, 1.0)
    dx, dy = point[:, 0] - q2[:, 0], point[:, 1] - q2[:, 1]
    l1 = ((q1[:, 1] - q2[:, 1]) * dx + (q2[:, 0] - q1[:, 0]) * dy) / safe
    l2 = ((q2[:, 1] - q0[:, 1]) * dx + (q0[:, 0] - q2[:, 0]) * dy) / safe
    l3 = 1.0 - l1 - l2
    hit = pierceable & (l1 >= -1e-9) & (l2 >= -1e-9) & (l3 >= -1e-9)
    z = l1 * depth[tri, 0] + l2 * depth[tri, 1] + l3 * depth[tri, 2]
    return (i * n + j)[hit], z[hit]


UP_AXES = ("y", "z")
CANONICAL_UP_AXIS = "z"


def normalize_up_axis(mesh, up_axis: str):
    """Bring a model's output into the pipeline's canonical Z-up space.

    Normalising here rather than at export keeps one orientation through the gate, the repair ladder
    and every measurement, so the only place orientation is reasoned about is this function and the
    single Z-to-Y rotation the glTF containers need.
    """
    axis = str(up_axis or CANONICAL_UP_AXIS).strip().lower()
    if axis not in UP_AXES:
        raise ExportError(
            f"Unknown output up axis '{up_axis}'; expected one of {', '.join(UP_AXES)}")
    if axis == CANONICAL_UP_AXIS:
        return mesh

    import numpy as np
    import trimesh

    rotated = mesh.copy()
    rotated.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2.0, [1, 0, 0]))
    logger.info(f"Rotated the generated mesh from {axis}-up into the pipeline's z-up space")
    return rotated


def gltf_oriented(mesh):
    """A copy of the mesh rotated from the pipeline's Z-up space into glTF's Y-up convention.

    The models reconstruct into a Z-up space, which OBJ and STL readers interpret as written. glTF
    declares Y as up, so the same vertex data written into a GLB renders lying on its side -- in the
    VAMS viewer, and in the preview thumbnail the preview pipeline renders from the GLB. Rotating on
    export keeps one orientation across all three formats as a viewer sees them, rather than leaving
    the geometry consistent and the presentation wrong.
    """
    import numpy as np
    import trimesh

    rotated = mesh.copy()
    rotated.apply_transform(
        trimesh.transformations.rotation_matrix(-np.pi / 2.0, [1, 0, 0]))
    return rotated


def welded_for_gltf(mesh):
    """A copy with coincident vertices merged and smooth normals, or the mesh unchanged if it has an atlas.

    A model that bakes a texture atlas splits every vertex at a uv seam, so its mesh reaches export with
    several vertices per position and per-face normals -- a smooth surface then renders faceted in a GL
    viewer, and the GLB carries several times the vertices it needs (a chart-packed mesh measured six to
    one). Merging coincident positions and recomputing per-vertex normals fixes both, and glTF carries
    the merged per-vertex colour array natively.

    It runs only for a mesh with NO atlas of its own. A TextureVisuals mesh keeps its uv seams SPLIT on
    purpose: merging them would leave the uv array addressing positions that no longer exist, so such a
    mesh is returned untouched -- which is why a chart-packed atlas is collapsed to per-vertex colour
    before it reaches here, and a contiguous one ships with its seams intact.

    The merged vertex takes the MEAN colour of its copies. Each copy was sampled on a different island's
    border, and keeping whichever copy came first shipped that border's speckle as the vertex's colour.
    """
    import numpy as np
    import trimesh

    if getattr(getattr(mesh, "visual", None), "kind", None) == "texture":
        return mesh
    unique, inverse = trimesh.grouping.unique_rows(mesh.vertices)
    welded = trimesh.Trimesh(vertices=np.asarray(mesh.vertices)[unique],
                             faces=inverse[np.asarray(mesh.faces)], process=False)
    colors = getattr(getattr(mesh, "visual", None), "vertex_colors", None)
    if colors is not None and len(colors) == len(mesh.vertices):
        colors = np.asarray(colors, dtype=float)
        total = np.zeros((len(unique), colors.shape[1]))
        count = np.zeros(len(unique))
        np.add.at(total, inverse, colors)
        np.add.at(count, inverse, 1.0)
        welded.visual = trimesh.visual.ColorVisuals(
            mesh=welded, vertex_colors=np.rint(total / count[:, None]).astype(np.uint8))
    return welded


def write_formats(mesh, output_dir: str, stem: str, formats, textured_variant=None) -> list:
    """Write the mesh in each requested format into ``output_dir``. Returns the paths written.

    OBJ is written through trimesh's exporter with its MTL and texture files alongside, so the
    material's relative ``map_Kd`` stays valid once the set is uploaded together. The glTF containers
    are written from a Y-up copy, welded to merge the uv-seam splits an atlas model leaves behind (see
    ``welded_for_gltf``); OBJ and STL are written as they are.

    ``textured_variant`` is an optional zero-argument callable returning a copy of the mesh whose colour
    has been baked into a uv atlas, used for the OBJ alone. The formats want different things from the
    same colour: glTF carries a per-vertex colour array natively and exactly, while an OBJ carries one
    only through a non-standard ``v x y z r g b`` extension that many readers ignore, so the OBJ gets a
    texture and an MTL naming it. It is a callable rather than a mesh so that the bake -- an unwrap and a
    rasterisation over every face -- is paid for only when an OBJ is actually being written.
    """
    os.makedirs(output_dir, exist_ok=True)
    written = []
    for fmt in formats:
        target = os.path.join(output_dir, f"{stem}.{fmt}")
        logger.info(f"Exporting {fmt.upper()}: {target}")
        if fmt == "obj":
            baked = textured_variant() if textured_variant is not None else None
            if baked is not None:
                logger.info("Writing the OBJ from a baked atlas rather than its vertex colour array")
            source = mesh if baked is None else baked
            # write_obj emits the MTL and any texture images next to the OBJ
            source.export(target, file_type="obj", include_texture=True)
        elif fmt in GLTF_FORMATS:
            gltf_oriented(welded_for_gltf(mesh)).export(target, file_type=fmt)
        else:
            mesh.export(target, file_type=fmt)
        if not os.path.isfile(target):
            raise ExportError(f"Export produced no {fmt.upper()} file at {target}")
        written.append(target)
    return written


def copy_texture_sidecars(texture_paths, output_dir: str) -> list:
    """Copy the model's raw texture files into ``output_dir/textures``. Returns the paths written.

    The degraded texture path: the maps ship as files of their own when they could not be carried
    onto the exported mesh, so the operator has the colour data even though the preview is grey.
    Material and geometry sidecars are left behind -- an MTL naming maps by their pre-cleanup
    relative names, or a glTF ``.bin`` buffer describing the pre-cleanup topology, resolves to
    nothing next to the exported set.
    """
    if not texture_paths or not output_dir:
        return []

    images = [p for p in texture_paths
              if os.path.splitext(str(p))[1].lower() in (".png", ".jpg", ".jpeg", ".ktx2")]
    if not images:
        logger.info("The model produced no texture images to copy alongside the mesh")
        return []

    destination_dir = os.path.join(output_dir, SIDECAR_SUBDIR)
    os.makedirs(destination_dir, exist_ok=True)
    copied = []
    for source in images:
        if not os.path.isfile(source):
            logger.warning(f"The texture file {source} is no longer present; not copied")
            continue
        destination = os.path.join(destination_dir, os.path.basename(source))
        shutil.copy2(source, destination)
        logger.info(f"Copied the texture file {source} -> {destination}")
        copied.append(destination)
    return copied


def upload_output_dir(s3_upload, local_dir: str, bucket: str, output_dir: str,
                      relative_subdir: str, skip_names=()) -> list:
    """Upload every file below ``local_dir``, preserving its internal structure.

    ``s3_upload`` is called as ``s3_upload(bucket, object_key, local_path)``. Names in
    ``skip_names`` are filtered out of the walk -- the gate report goes to the metadata prefix under
    its own name and must not also land among the asset's files.
    """
    if not bucket:
        raise ExportError("An output bucket is required to upload the generated files")

    skip = set(skip_names or ())
    uploaded = []
    for root, _dirs, files in os.walk(local_dir):
        for filename in sorted(files):
            if filename in skip:
                continue
            local_path = os.path.join(root, filename)
            relative_path = os.path.relpath(local_path, local_dir).replace(os.sep, "/")
            object_key = compose_output_key(output_dir, relative_subdir, relative_path)
            s3_upload(bucket, object_key, local_path)
            logger.info(f"Uploaded: s3://{bucket}/{object_key}")
            uploaded.append(object_key)
    if not uploaded:
        raise ExportError(f"No output files were produced in {local_dir}")
    return uploaded


def metadata_report_key(metadata_dir: str, relative_subdir: str) -> str:
    """The key the run's metadata document must use for the write-back to consume it.

    The write-back reads only keys ending in ``.metadata.json`` under the metadata prefix, and treats
    the stem before that suffix as the asset-relative path of the FILE the metadata attaches to --
    ``boopy.glb.metadata.json`` applies to ``boopy.glb``, and the write-back fails outright when that
    file is absent from the output. The one exception is the reserved name ``asset.metadata.json``,
    which applies its entries to the asset instead of to a file.

    This report describes the run: the model, the settings, the gate result and the benchmark. It
    belongs on the asset, and naming it after the input image would point the write-back at a file
    that never exists in the output, since the outputs are meshes rather than the source photograph.
    """
    return compose_output_key(
        metadata_dir, relative_subdir, ASSET_METADATA_FILENAME)


def file_metadata_key(metadata_dir: str, relative_subdir: str, filename: str) -> str:
    """The key a metadata document must use to attach to one output FILE rather than to the asset.

    ``mug.glb`` takes ``mug.glb.metadata.json``. The write-back FAILS when the named file is absent from
    the output, so a document may only be written for a file the run actually exported.
    """
    return compose_output_key(metadata_dir, relative_subdir, f"{filename}{METADATA_SUFFIX}")


def carries_colour_in_file(filename: str) -> bool:
    """Whether this output format stores colour at all."""
    return os.path.splitext(filename)[1].lstrip(".").lower() in COLOUR_CARRYING_FORMATS


def build_metadata_document(entries: dict) -> dict:
    """The write-back document shape for a set of metadata key/value pairs."""
    metadata = []
    for key, value in entries.items():
        metadata.append({
            "metadataKey": key,
            "metadataValue": value if isinstance(value, str) else _as_text(value),
            "metadataValueType": "string",
        })
    return {"metadata": metadata, "updateType": "update", "type": "metadata"}


def _as_text(value) -> str:
    """A metadata value rendered as the string the write-back stores."""
    import json

    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value)
    return str(value)
