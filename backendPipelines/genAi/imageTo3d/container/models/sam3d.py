# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Meta SAM 3D Objects adapter.

SAM 3D Objects reconstructs shape, texture and layout for an object in a natural photo, so it is the
most tolerant of the models to a cluttered, uncontrolled snapshot. The weights are distributed
under the Meta SAM License, which carries trade-control restrictions and a redistribute-under-the-
same-terms obligation, and the repo is gated on HuggingFace: the run needs an HF_TOKEN whose account
has accepted the licence, and the resulting image must not be redistributed outside those terms.
"""

import json
import os
from pathlib import Path

from vams_utils.logging import log

from .base import (
    GenerationResult,
    ModelAdapter,
    collect_sidecars,
    find_generated_mesh,
    run_upstream,
    setting,
)

logger = log.get_logger()

# The run's measurements, written by the driver beside the mesh and carried onto the execution as a
# diagnostic: what the interior fill did to the vertex grid, the raw and exported mesh sizes, how many
# exported vertices carry the mid grey of an uncoloured vertex, the sampler values the pipeline
# configuration resolved to, the GPU that ran and the upstream revision it ran.
STATS_FILENAME = "sam3d_stats.json"

# The extractor's vertex grid as the model seeded it, written by the driver on request and carried
# onto the run as a bulky diagnostic: the active mask bit-packed, the signed distance as float16 and
# the grid resolution.
FIELD_FILENAME = "sam3d_field.npz"

# Driver run inside the upstream repo. The inference code is not part of the installed package: the
# repo's own demo puts ``notebook`` on the path and imports from there, and the pipeline is configured
# by a yaml under the checkpoint directory the gated HuggingFace snapshot provides.
DRIVER_TEMPLATE = '''
import importlib
import json
import os
import subprocess
import sys

from PIL import Image

image_path = sys.argv[1]
output_dir = sys.argv[2]
settings = json.loads(sys.argv[3])

os.makedirs(output_dir, exist_ok=True)

# The sampler settings the pipeline configuration resolved to, read back off the constructed pipeline
# for the run's record.
SAMPLER_ATTRIBUTES = (
    "ss_inference_steps", "slat_inference_steps", "ss_cfg_strength", "ss_cfg_strength_pm",
    "slat_cfg_strength", "ss_rescale_t", "slat_rescale_t", "ss_cfg_interval", "slat_cfg_interval",
)


def solid_interior_sdf(active, sdf, opening, sigma=0.06, max_radius=6, skin_directions=6):
    """The dense SDF grid with every positive region the surface seals off reading as inside.

    The FlexiCubes vertex grid the mesh is extracted from is seeded +1 at every vertex the sparse
    structure does not carry, and the structure is a band around the surface rather than a filled
    volume, so across the band's few voxels the field reads outside, inside and outside again, and
    then outside through the whole of the object's interior: the extractor emits a second,
    inward-facing wall inside the outer one, sealing a void of about a third of the object's volume.

    ``active`` marks the vertices the structure carries; ``sdf`` is the field over the same grid. The
    outside is what a flood from the grid boundary reaches through positive vertices, and every
    positive vertex it does not reach is made negative. The flood runs through the positive region eroded
    by ``opening`` vertices and its reach is then dilated back by the same amount: an opening that
    seals a puncture in the model's negative layer up to twice the opening wide, so the flood cannot
    pour through it into the interior, at the cost of bridging an exterior gap that narrow with a wall
    at its mouth. Erosion, dilation and the flood through the eroded region all use the full
    26-neighbourhood (at an opening of zero the flood is 6-connected), and the field is padded with
    positive vertices by one more than the largest radius before any of them runs, so the space beyond
    the grid is outside: an object standing on a face of the grid keeps the floor under it, a gap
    between the object and a face stays open, and an inward corner of the surface is not filled in.
    Every vertex the dilation hands back lies in an all-positive cube around one the flood reached:
    the fill never reaches through a wall the flood could not. A puncture wider than twice the opening
    lets the flood reach the interior, which then stays as the model wrote it: the fill can only ever
    move vertices inward.

    The widest puncture differs from one reconstruction to the next, so ``opening`` is either that
    radius or ``"auto"``. Under ``"auto"`` the radius grows from zero one vertex at a time, up to
    ``max_radius``, and each step's growth in the flipped count is read against the number of active
    vertices: shutting the flood out of a crease flips a sliver, shutting it out of the interior
    flips a volume, so a step that flips at least ``sigma`` of the active count has just sealed the
    band. The search ends at the first step after a seal that does not seal, and the radius settles
    on the last of that first run of sealing steps, or on zero when no step seals, so a band the fill
    cannot close is left as the model wrote it rather than bridged ever wider in the attempt.

    An opening that seals a puncture also shuts the flood out of every exterior gap as narrow, between
    two legs or behind an ear, so what it flips is then read against the outer skin, the part of the
    surface the outside can see (``skin_winding``, over rays along ``skin_directions`` or more of the 26
    lattice directions). A flipped vertex whose skin winding is below one half lies outside the skin
    and is handed back to the outside. Of the rest, the largest 6-connected piece is the interior and
    stays, as does every piece with no 6-neighbour outside, while any other piece touching the outside
    is the mouth of a gap and is handed back too unless it lies well inside the skin
    (``interior_components``). Each vertex still flipped is written as the negative of its own
    magnitude, so a band vertex holding a small positive value crosses zero where the model's surface
    already was, and a seeded +1 becomes -1. Last, a positive vertex the hand-backs leave cut off from
    the outside is flipped after all, so the field returned seals no void.

    Returns the rewritten field and the counts describing what was done to it. ``opening`` is the
    radius the fill ran at and ``flippedByRadius`` the flipped count at each radius it measured.
    ``openingFlippedVertices`` is what the opening left unreached; ``outsideSkinVertices`` and
    ``exteriorPieceVertices`` are the vertices the skin and the piece test handed back,
    ``exemptPieces`` the touching pieces kept as interior, and ``closureVertices`` those flipped last.
    ``exteriorVertices``, ``flippedVertices`` and the counts after it describe the field returned.
    ``bridgedVertices`` are the flipped vertices 6-adjacent to the outside, where the rewritten field
    puts a crossing the model did not: sealed punctures and bridged gaps, and nothing else.
    """
    import time

    import numpy as np
    from scipy import ndimage

    started = time.time()
    six = ndimage.generate_binary_structure(3, 1)
    cube = ndimage.generate_binary_structure(3, 3)
    active = np.asarray(active, dtype=bool)
    sdf = np.array(sdf, dtype=np.float32, copy=True)
    positive = sdf > 0

    adaptive = str(opening).strip().lower() == "auto"
    if adaptive:
        if int(max_radius) < 0:
            raise ValueError("max_radius is a radius in grid vertices of zero or more, not " + repr(max_radius))
        radii = range(int(max_radius) + 1)
        threshold = float(sigma) * int(active.sum())
    elif int(opening) < 0:
        raise ValueError("the opening is a radius in grid vertices or 'auto', not " + repr(opening))
    else:
        radii = (int(opening),)

    # A field whose positive region touches no face of the grid has no outside to flood from and is
    # left as it is.
    has_outside = any(np.take(positive, [0, -1], axis=axis).any() for axis in range(3))
    # The space beyond the grid is outside: padded by one more than the largest radius, its outer
    # layer survives every erosion and the flood is seeded from it.
    pad = max(radii) + 1
    core = (slice(pad, -pad),) * 3
    positive = np.pad(positive, pad, constant_values=True)

    def reach(eroded, radius):
        """The flood's reach through ``eroded``, dilated back by ``radius`` within the positive region."""
        if not has_outside:
            return positive
        # Two eroded vertices a cube apart share an all-positive cube, so the eroded region is
        # labelled with the neighbourhood the erosion used and no component the flood joins is split.
        labels, _ = ndimage.label(eroded, structure=cube if radius > 0 else six)
        exterior = labels == labels[0, 0, 0]
        if radius > 0:
            exterior = ndimage.binary_dilation(exterior, structure=cube, iterations=radius) & positive
        return exterior

    eroded, eroded_by = positive, 0
    flipped_by_radius = []
    chosen = None
    sealed = False
    for radius in radii:
        if radius > eroded_by:
            eroded = ndimage.binary_erosion(eroded, structure=cube, iterations=radius - eroded_by,
                                            border_value=1)
            eroded_by = radius
        exterior = reach(eroded, radius)
        flipped_by_radius.append(int((positive & ~exterior)[core].sum()))
        if not adaptive or radius == 0:
            chosen = (radius, eroded, exterior)
        elif flipped_by_radius[-1] - flipped_by_radius[-2] >= threshold:
            sealed = True
            chosen = (radius, eroded, exterior)
        elif sealed:
            break
    radius, eroded, exterior = chosen
    flipped = positive & ~exterior
    positive, eroded, flipped = (array[core] for array in (positive, eroded, flipped))
    opening_flipped = int(flipped.sum())

    outside_skin = exterior_pieces = exempt = closure = 0
    if opening_flipped:
        winding = skin_winding(sdf, skin_directions)
        inside = flipped & (winding >= 0.5)
        outside_skin = opening_flipped - int(inside.sum())
        flipped, exempt = interior_components(inside, positive, winding)
        exterior_pieces = int((inside & ~flipped).sum())
    sdf[flipped] = -np.abs(sdf[flipped])
    if outside_skin or exterior_pieces:
        labels, _ = ndimage.label(np.pad(sdf > 0, 1, constant_values=True), structure=six)
        sealed = (labels[1:-1, 1:-1, 1:-1] != labels[0, 0, 0]) & (sdf > 0)
        closure = int(sealed.sum())
        sdf[sealed] = -np.abs(sdf[sealed])
        flipped = flipped | sealed
    exterior = positive & ~flipped
    bridged = flipped & ndimage.binary_dilation(np.pad(exterior, 1, constant_values=True),
                                                structure=six)[1:-1, 1:-1, 1:-1]

    components, count = ndimage.label(flipped, structure=six)
    sizes = np.bincount(components.ravel())[1:] if count else np.zeros(0, dtype=np.int64)

    return sdf, {
        "gridRes": int(active.shape[0]),
        "opening": int(radius),
        "openingRequested": "auto" if adaptive else int(opening),
        "flippedByRadius": flipped_by_radius,
        "activeVertices": int(active.sum()),
        "positiveVertices": int(positive.sum()),
        "erodedVertices": int(eroded.sum()),
        "exteriorVertices": int(exterior.sum()),
        "openingFlippedVertices": opening_flipped,
        "outsideSkinVertices": outside_skin,
        "exteriorPieceVertices": exterior_pieces,
        "exemptPieces": exempt,
        "closureVertices": closure,
        "flippedVertices": int(flipped.sum()),
        "flippedActive": int((flipped & active).sum()),
        "flippedComponents": int(count),
        "largestFlippedComponent": int(sizes.max()) if count else 0,
        "bridgedVertices": int(bridged.sum()),
        "elapsedSeconds": round(time.time() - started, 3),
    }


def skin_winding(sdf, min_directions=6, margin=16):
    """The winding number of the field's outer skin at every vertex: near 0 outside it, near 1 inside.

    The outer skin is the part of the surface the outside can see: every grid edge from a positive
    vertex to a negative one whose positive end rays from beyond the grid reach along at least
    ``min_directions`` of the 26 lattice directions (``ray_reach``, over the field padded with one
    positive layer). Each such edge carries a unit of flux out of the surface, and the winding number
    solves the Poisson equation whose source is that flux's divergence, by FFT on a periodic box at
    least ``margin`` vertices wider than the padded field on every side, shifted to read zero on the
    box faces. Were every edge skin, it would read exactly 1 at negative vertices and 0 at positive
    ones. A wall the outside cannot see adds nothing, so a region the visible skin encloses reads near
    1 whatever the model wrote inside it, and a gap the outside sees into reads near 0.
    """
    import numpy as np
    import scipy.fft

    positive = np.pad(np.asarray(sdf) > 0, 1, constant_values=True)
    reach = ray_reach(positive)
    divergence = np.zeros(positive.shape, dtype=np.float32)
    for axis in range(3):
        low = tuple(slice(0, -1) if i == axis else slice(None) for i in range(3))
        high = tuple(slice(1, None) if i == axis else slice(None) for i in range(3))
        flux = np.zeros(positive[low].shape, dtype=np.float32)
        flux[positive[low] & ~positive[high] & (reach[low] >= min_directions)] = -1.0
        flux[~positive[low] & positive[high] & (reach[high] >= min_directions)] = 1.0
        divergence[low] += flux
        divergence[high] -= flux

    def fft_length(length):
        """The smallest length of at least ``length`` with no prime factor above 5."""
        while True:
            rest = length
            for factor in (2, 3, 5):
                while rest % factor == 0:
                    rest //= factor
            if rest == 1:
                return length
            length += 1

    box = [fft_length(length + 2 * margin) for length in divergence.shape]
    block = tuple(slice((side - length) // 2, (side - length) // 2 + length)
                  for side, length in zip(box, divergence.shape))
    source = np.zeros(box, dtype=np.float32)
    source[block] = -divergence
    spectrum = scipy.fft.rfftn(source, workers=-1)
    # The 7-point Laplacian's eigenvalues on the periodic box; the mean is left at zero.
    kx = np.fft.fftfreq(box[0]).astype(np.float32)
    ky = np.fft.fftfreq(box[1]).astype(np.float32)
    kz = np.fft.rfftfreq(box[2]).astype(np.float32)
    lx, ly, lz = ((2 * np.cos(2 * np.pi * k) - 2).astype(np.float32) for k in (kx, ky, kz))
    eigenvalues = lx[:, None, None] + ly[None, :, None] + lz[None, None, :]
    eigenvalues[0, 0, 0] = 1.0
    inverse = 1.0 / eigenvalues
    inverse[0, 0, 0] = 0.0
    spectrum *= inverse.astype(spectrum.dtype)
    winding = scipy.fft.irfftn(spectrum, s=box, workers=-1).astype(np.float32)
    faces = np.concatenate([winding[0].ravel(), winding[-1].ravel(), winding[:, 0].ravel(),
                            winding[:, -1].ravel(), winding[:, :, 0].ravel(), winding[:, :, -1].ravel()])
    winding -= float(np.mean(faces))
    return winding[block][1:-1, 1:-1, 1:-1]


def ray_reach(positive):
    """How many of the 26 lattice directions a ray from beyond the grid reaches each vertex along.

    A ray steps one lattice vector at a time through positive vertices and stops at the first
    negative one. A diagonal step passes only where a 6-connected path of positive vertices joins its
    two ends inside their unit cell, so no ray slips between two negatives that meet at an edge or a
    corner. Everything beyond ``positive`` counts as positive, so rays enter from every side.
    """
    import numpy as np

    def shifted(array, offset):
        """``array`` moved by ``offset`` vertices along each axis, positive where the move leaves it."""
        out = np.ones(array.shape, dtype=bool)
        target = tuple(slice(max(step, 0), length + min(step, 0)) for length, step in zip(array.shape, offset))
        source = tuple(slice(max(-step, 0), length - max(step, 0)) for length, step in zip(array.shape, offset))
        out[target] = array[source]
        return out

    positive = np.asarray(positive, dtype=bool)
    counts = np.zeros(positive.shape, dtype=np.uint8)
    directions = [(a, b, c) for a in (-1, 0, 1) for b in (-1, 0, 1) for c in (-1, 0, 1) if a or b or c]
    for direction in directions:
        # Swept plane by plane along the first axis the direction moves on, through views oriented so
        # the sweep runs forward, so the counts land in place.
        axis = next(i for i in range(3) if direction[i])
        order = [axis] + [i for i in range(3) if i != axis]
        view, count_view = np.transpose(positive, order), np.transpose(counts, order)
        across, along = direction[order[1]], direction[order[2]]
        if direction[axis] < 0:
            view, count_view = view[::-1], count_view[::-1]
        if not across and not along:
            passable = view
        elif not across or not along:
            side = (0, across, 0) if not along else (0, 0, along)
            passable = view & (shifted(view, side) | shifted(view, (1, 0, 0)))
        else:
            first = (shifted(view, (0, across, along)), shifted(view, (1, 0, along)),
                     shifted(view, (1, across, 0)))
            second = (shifted(view, (0, 0, along)), shifted(view, (0, across, 0)), shifted(view, (1, 0, 0)))
            passable = view & ((first[0] & (second[0] | second[1])) | (first[1] & (second[0] | second[2]))
                               | (first[2] & (second[1] | second[2])))
        reached = np.empty(view.shape, dtype=bool)
        reached[0] = passable[0]
        entering = np.empty(view.shape[1:], dtype=bool)
        target = (slice(max(across, 0), view.shape[1] + min(across, 0)),
                  slice(max(along, 0), view.shape[2] + min(along, 0)))
        source = (slice(max(-across, 0), view.shape[1] - max(across, 0)),
                  slice(max(-along, 0), view.shape[2] - max(along, 0)))
        for plane in range(1, view.shape[0]):
            # A ray entering this plane through a side of the grid comes from beyond it.
            entering.fill(True)
            entering[target] = reached[plane - 1][source]
            np.logical_and(passable[plane], entering, out=reached[plane])
        count_view += reached
    return counts


def interior_components(inside, positive, winding, core_winding=0.9, core_contact=0.25):
    """``inside`` without its pieces that are the mouths of exterior gaps rather than interior.

    Of the 6-connected pieces of ``inside``, the largest is the object's interior and stays, as does
    every piece with no 6-neighbour among the positive vertices ``inside`` leaves out. A piece that
    touches them stays only when it lies well inside the skin: a mean ``winding`` of at least
    ``core_winding`` with at most ``core_contact`` of its vertices touching. Returns the vertices kept
    and the number of touching pieces kept by that last test.
    """
    import numpy as np
    from scipy import ndimage

    six = ndimage.generate_binary_structure(3, 1)
    labels, count = ndimage.label(inside, structure=six)
    if not count:
        return inside, 0
    sizes = np.bincount(labels.ravel(), minlength=count + 1)
    sizes[0] = 0
    outside = positive & ~inside
    contact = np.bincount(labels[inside & ndimage.binary_dilation(outside, structure=six)].ravel(),
                          minlength=count + 1)
    mean_winding = np.bincount(labels.ravel(), weights=winding.ravel().astype(np.float64),
                               minlength=count + 1) / np.maximum(sizes, 1)
    touching = contact > 0
    core = (mean_winding >= core_winding) & (contact <= core_contact * sizes)
    largest = np.arange(count + 1) == int(sizes.argmax())
    keep = largest | ~touching | core
    keep[0] = False
    return keep[labels] & inside, int((touching & ~largest & core)[1:].sum())


def fill_colour_sources(active, before, after):
    """The seeded ends of the crossings the fill created, and the carried vertex each copies from.

    FlexiCubes interpolates a crossing's colour and normal from the channels its two end vertices hold
    after the SDF and the displacement. A vertex the sparse structure does not carry holds zeros there,
    which the extractor decodes as mid grey, and a crossing the fill creates, between a flipped vertex
    and one left positive, can have such an end. Each is paired with the nearest vertex the structure
    carries. Returns the two as flat indices into the grid, in step.
    """
    import numpy as np
    from scipy.spatial import cKDTree

    active = np.asarray(active, dtype=bool)
    positive = np.asarray(after) > 0
    flipped = (np.asarray(before) > 0) & ~positive
    ends = np.zeros(positive.shape, dtype=bool)
    for axis in range(3):
        low = tuple(slice(0, -1) if i == axis else slice(None) for i in range(3))
        high = tuple(slice(1, None) if i == axis else slice(None) for i in range(3))
        created = (positive[low] != positive[high]) & (flipped[low] | flipped[high])
        ends[low] |= created
        ends[high] |= created
    targets = np.flatnonzero(ends & ~active)
    carried = np.flatnonzero(active)
    if not len(targets) or not len(carried):
        return targets[:0], targets[:0]
    tree = cKDTree(np.column_stack(np.unravel_index(carried, positive.shape)))
    _, nearest = tree.query(np.column_stack(np.unravel_index(targets, positive.shape)))
    return targets, carried[nearest]


def vertex_grid(coords, dense, res):
    """The active mask and the seeded SDF of one vertex grid, as arrays over the full grid."""
    import numpy as np

    index = coords.detach().cpu().numpy().astype(np.int64)
    active = np.zeros((res, res, res), dtype=bool)
    active[index[:, 0], index[:, 1], index[:, 2]] = True
    sdf = dense[:, 0].detach().float().cpu().numpy().reshape(res, res, res)
    return active, sdf


def extractor_seeding(setting_name):
    """The module the FlexiCubes extractor seeds its vertex grid through, and the function it uses.

    SparseFeatures2Mesh densifies its vertex attributes through the get_dense_attrs its module took
    from utils_cube with a star import, and resolves the name at call time, so rebinding it on that
    module reaches the extractor without touching the installed source. A run that asked for the
    seeding to be rewritten stops here when upstream has moved it.
    """
    module_name = "sam3d_objects.model.backbone.tdfy_dit.representations.mesh.cube2mesh"
    try:
        cube2mesh = importlib.import_module(module_name)
    except ImportError as e:
        cube2mesh = None
        cause = repr(e)
    else:
        cause = "the module lacks get_dense_attrs or SparseFeatures2Mesh"
    original = getattr(cube2mesh, "get_dense_attrs", None)
    if original is None or not hasattr(cube2mesh, "SparseFeatures2Mesh"):
        raise RuntimeError(
            setting_name + " is on but the mesh extraction it rewrites is not where this driver "
            "expects it (" + module_name + ": " + cause + "). Upstream has moved; the run stops rather "
            "than deliver a mesh the setting was asked to change."
        )
    return cube2mesh, original


# The files the field dump wrote, one per vertex grid the extractor seeded.
field_dumps = []


def install_field_dump(path):
    """Write each vertex grid the extractor seeds to ``path`` before anything else reads it.

    Only the vertex-grid call, the one that seeds the SDF, is recorded; the cube-weight call passes
    through untouched. Installed ahead of the interior fill, so the field on disk is the one the model
    seeded and not the one the fill hands back. A second grid in one run goes to a numbered file.
    """
    import numpy as np

    cube2mesh, original = extractor_seeding("SAM3D_DUMP_FIELD")

    def get_dense_attrs_dumping(coords, feats, res, sdf_init=True):
        dense = original(coords, feats, res, sdf_init)
        if not sdf_init:
            return dense
        active, sdf = vertex_grid(coords, dense, res)
        target = path if not field_dumps else path[:-4] + "-" + str(len(field_dumps) + 1) + ".npz"
        np.savez_compressed(target, active=np.packbits(active), sdf=sdf.astype(np.float16),
                            res=np.int64(res))
        field_dumps.append(target)
        print("SAM 3D Objects wrote " + target)
        return dense

    cube2mesh.get_dense_attrs = get_dense_attrs_dumping


# One entry per vertex grid the fill rewrote, in the order the extractor asked for them.
solid_interior_runs = []


def install_solid_interior(opening):
    """Route the FlexiCubes extractor's SDF seeding through solid_interior_sdf.

    Only the vertex-grid call, the one that seeds the SDF, is rewritten; the cube-weight call passes
    through untouched. Each seeded end of a crossing the fill created takes the colour and normal
    channels of the nearest vertex the structure carries (``fill_colour_sources``).
    """
    import torch

    cube2mesh, original = extractor_seeding("SAM3D_SOLID_INTERIOR")

    def get_dense_attrs_solid(coords, feats, res, sdf_init=True):
        dense = original(coords, feats, res, sdf_init)
        if not sdf_init:
            return dense
        active, before = vertex_grid(coords, dense, res)
        sdf, stats = solid_interior_sdf(active, before, opening)
        dense[:, 0] = torch.as_tensor(sdf.reshape(-1), device=dense.device).to(dense.dtype)
        targets, sources = fill_colour_sources(active, before, sdf) if dense.shape[1] > 4 else ((), ())
        if len(targets):
            index = torch.as_tensor(targets, device=dense.device)
            dense[index, 4:] = dense[torch.as_tensor(sources, device=dense.device), 4:]
        stats["colourSeededVertices"] = int(len(targets))
        solid_interior_runs.append(stats)
        print("SAM 3D Objects solid interior: " + json.dumps(stats))
        return dense

    cube2mesh.get_dense_attrs = get_dense_attrs_solid


def jsonable(value):
    """``value`` in a form json.dumps accepts: arrays and containers walked, anything else stringified."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if hasattr(value, "__iter__"):
        return [jsonable(v) for v in value]
    return str(value)


def mesh_counts(mesh):
    """Vertex and face counts of a MeshExtractResult or a trimesh, or None where there is no mesh."""
    if mesh is None:
        return None
    return {"vertices": int(len(mesh.vertices)), "faces": int(len(mesh.faces))}


def grey_vertices(mesh):
    """How many vertices carry (128, 128, 128), the colour the extractor decodes from zero, or None
    where the mesh carries no vertex colours."""
    import numpy as np

    colours = getattr(getattr(mesh, "visual", None), "vertex_colors", None)
    if colours is None or not len(colours):
        return None
    return int((np.asarray(colours)[:, :3] == 128).all(axis=1).sum())


def gpu_name():
    """The GPU the run used, as CUDA names it, or the reason it could not be read."""
    try:
        import torch

        return torch.cuda.get_device_name(0)
    except Exception as e:
        return "unreadable: " + repr(e)


def upstream_commit(repo_dir):
    """The revision of the upstream checkout that ran, or the reason it could not be read."""
    try:
        result = subprocess.run(["git", "-C", repo_dir, "rev-parse", "HEAD"],
                                capture_output=True, text=True, timeout=30)
    except Exception as e:
        return "unreadable: " + repr(e)
    if result.returncode != 0:
        return "unreadable: " + result.stderr.strip()
    return result.stdout.strip()


# Upstream API: facebook/sam-3d-objects demo.py -- sys.path.append("notebook"), then Inference on a
# pipeline yaml under checkpoints/. Confirm against the commit pinned by SAM3D_COMMIT in
# Dockerfile.sam3d before deploying.
sys.path.append(os.path.join(settings["repo_dir"], "notebook"))
from inference import Inference

# The dump goes in first, so the field it writes is the one the model seeded and not the fill's.
if settings["dump_field"]:
    install_field_dump(os.path.join(output_dir, "sam3d_field.npz"))
if settings["solid_interior"]:
    install_solid_interior(settings["solid_interior_opening"])

inference = Inference(settings["config_path"], compile=False)

image = Image.open(image_path)
if image.mode != "RGBA":
    image = image.convert("RGBA")

# The mask travels in the alpha channel, which is what the pipeline expects: its own wrapper builds an
# RGBA image from a separate image + mask pair and then passes no mask through. A cutout from the
# pipeline's segmentation stage already carries it, so the mask argument stays None.
#
# The pipeline is driven directly rather than through Inference.__call__ so the flags below are stated
# here, but they deliberately match that wrapper's values. Both of upstream's postprocess stages
# rasterize through utils3d, whose torch rasterization module imports nvdiffrast at module scope:
# with_mesh_postprocess reaches it through postprocess_mesh -> _fill_holes, and with_texture_baking
# through bake_texture's own RastContext. nvdiffrast is NVIDIA-licensed and is not installed here, so
# either flag set true ends the run with ModuleNotFoundError after the GPU work is already done.
# Setting rendering_engine to pytorch3d, which upstream's Inference does, only redirects one renderer
# and does not avoid those two paths.
#
# Nothing is lost by declining them: simplification and hole filling are what this pipeline's own mesh
# cleanup ladder does, under a print gate that measures the result. The mesh arrives carrying vertex
# colours instead of a baked atlas.
outputs = inference._pipeline.run(
    image,
    None,
    settings["seed"],
    with_mesh_postprocess=False,
    with_texture_baking=False,
    use_vertex_color=True,
)

if settings["solid_interior"] and not solid_interior_runs:
    raise RuntimeError(
        "SAM3D_SOLID_INTERIOR is on but the mesh extraction never reached the rewritten seeding: "
        "upstream's decoder no longer densifies its vertex grid through cube2mesh.get_dense_attrs, and "
        "the mesh it produced would carry the inner wall the setting exists to remove."
    )
if settings["dump_field"] and not field_dumps:
    print("SAM3D_DUMP_FIELD is on but the mesh extraction never reached the recorded seeding; "
          "no field was written and the mesh stands")

glb = outputs.get("glb")
if glb is None:
    raise RuntimeError(
        "SAM 3D Objects returned no glb; the run decoded "
        + ", ".join(sorted(k for k in outputs)) + " but mesh postprocessing produced nothing"
    )

mesh_path = os.path.join(output_dir, "sam3d.glb")
glb.export(mesh_path)
print("SAM 3D Objects wrote " + mesh_path)

# The raw extraction is a list of one MeshExtractResult; the glb is the same mesh after upstream's
# own conversion, so the two counts differ by whatever that conversion welded or dropped. The mesh
# is already on disk, so a failure in the bookkeeping is reported and the run keeps its mesh.
try:
    raw = outputs.get("mesh")
    if isinstance(raw, (list, tuple)):
        raw = raw[0] if raw else None
    stats = {
        "solidInterior": solid_interior_runs[-1] if solid_interior_runs else "off",
        "solidInteriorInvocations": len(solid_interior_runs),
        "fieldDump": [os.path.basename(path) for path in field_dumps],
        "rawMesh": mesh_counts(raw),
        "glb": mesh_counts(glb),
        "greyVertices": grey_vertices(glb),
        "gpu": gpu_name(),
        "sampler": {name: jsonable(getattr(inference._pipeline, name, None))
                    for name in SAMPLER_ATTRIBUTES},
        "upstreamCommit": upstream_commit(settings["repo_dir"]),
    }
    stats_path = os.path.join(output_dir, "sam3d_stats.json")
    with open(stats_path, "w", encoding="utf-8") as handle:
        json.dump(stats, handle, indent=2)
    print("SAM 3D Objects wrote " + stats_path)
except Exception as e:
    print("SAM 3D Objects could not record its measurements (" + repr(e) + "); the mesh stands")
'''


def solid_interior_opening(value):
    """``SAM3D_SOLID_INTERIOR_OPENING`` as the driver takes it: ``"auto"`` or a radius in grid vertices."""
    text = str(value).strip()
    if text.lower() == "auto":
        return "auto"
    try:
        radius = int(text)
    except ValueError:
        radius = -1
    if radius < 0:
        raise ValueError(
            f"SAM3D_SOLID_INTERIOR_OPENING is 'auto' or a radius of zero or more grid vertices, not {value!r}"
        )
    return radius


class Sam3dAdapter(ModelAdapter):
    KEY = "sam3d"
    DISPLAY_NAME = "Meta SAM 3D Objects"
    LICENSE = "Meta SAM License (trade-control and redistribute-same-terms obligations)"
    MIN_VRAM_GB = 24
    # Two repos: the gated weights, and the MoGe monocular-depth model the pipeline configuration
    # names as its depth stage. MoGe (Apache-2.0, ungated) is instantiated by hydra from the yaml
    # rather than by anything here, so without pre-fetching it a run downloads it partway through
    # inference -- on a GPU instance, after the image pull, and reported as a hydra failure.
    HF_REPOS = ("facebook/sam-3d-objects", "Ruicheng/moge-vitl")
    REPO_DIR = "/opt/sam3d"

    # The repo holding the weights and the pipeline configuration, named rather than taken by position
    # so adding a dependency repo above cannot change which one the configuration is read from.
    CONFIG_REPO = "facebook/sam-3d-objects"
    # The pipeline configuration inside that repo, relative to the snapshot root. The repo ships one
    # yaml describing the whole pipeline alongside the per-stage checkpoints it names.
    CONFIG_RELPATH = "checkpoints/pipeline.yaml"

    # The pipeline reads the object mask out of the alpha channel, so the cutout must reach it whole.
    FRAMING_BACKGROUND = "none"

    # Upstream reconstructs into a Z-up space and then rotates the vertices to Y-up on the way out of
    # to_glb, unconditionally -- the rotation sits outside the branch that selects the output's colour
    # form, so it applies to every configuration.
    OUTPUT_UP_AXIS = "y"

    def ensure_ready(self, hf_home: str) -> None:
        # The weights are gated, so a missing token produces a 401 from inside the upstream
        # predictor rather than anything an operator can act on. Name the cause here instead.
        if not os.environ.get("HF_TOKEN"):
            raise RuntimeError(
                "Meta SAM 3D Objects weights are gated on HuggingFace. Set "
                "app.pipelines.useImageTo3d.huggingFaceToken to a token whose account has accepted "
                "the Meta SAM License for facebook/sam-3d-objects. The licence also carries "
                "trade-control restrictions and requires that any redistribution keep the same "
                "terms."
            )
        super().ensure_ready(hf_home)

    def resolve_config_path(self, params: dict) -> str:
        """Locate the pipeline configuration inside the fetched weights snapshot.

        The config is read from within the snapshot rather than copied elsewhere, because upstream's
        ``Inference`` sets its workspace directory to the config file's own parent and resolves every
        checkpoint the yaml names relative to it. A copy outside the snapshot resolves no weights.
        """
        snapshot = Path(self.snapshot_dir(self.CONFIG_REPO))
        relpath = str(setting(params, "SAM3D_CONFIG_RELPATH", self.CONFIG_RELPATH))
        config = snapshot / relpath
        if config.is_file():
            logger.info(f"SAM 3D Objects pipeline configuration resolved to {config}")
            return str(config)

        # Name what the snapshot actually holds. The repo is gated, so an operator hitting this
        # cannot list it themselves without accepting the licence first.
        found = sorted(str(p.relative_to(snapshot)) for p in snapshot.rglob("*.yaml"))
        raise RuntimeError(
            f"The SAM 3D Objects pipeline configuration is not at '{relpath}' inside the weights "
            f"snapshot {snapshot}. The yaml files it does carry: "
            f"{', '.join(found) if found else '(none)'}. Set SAM3D_CONFIG_RELPATH to the "
            f"configuration's path within the repo if upstream has moved it."
        )

    def generate(self, image_path: str, work_dir: str, params: dict,
                 extra_images=(), source_images=(), starting_mesh=None) -> GenerationResult:
        out_dir = self.output_dir(work_dir)

        # The SAM3D_* keys are this model's own block in config.json, so a sam3d run accepts them and
        # a run on another model does not. The defaults below match that block.
        driver_settings = {
            "hf_repo": self.CONFIG_REPO,
            "repo_dir": self.REPO_DIR,
            "config_path": self.resolve_config_path(params),
            "seed": int(setting(params, "SAM3D_SEED", 1)),
            "solid_interior": str(setting(params, "SAM3D_SOLID_INTERIOR", "true")).strip().lower() == "true",
            "solid_interior_opening": solid_interior_opening(
                setting(params, "SAM3D_SOLID_INTERIOR_OPENING", "auto")),
            "dump_field": str(setting(params, "SAM3D_DUMP_FIELD", "false")).strip().lower() == "true",
        }

        driver_path = Path(work_dir) / "sam3d_driver.py"
        driver_path.write_text(DRIVER_TEMPLATE, encoding="utf-8")

        run_upstream(
            [
                "python", "-u", str(driver_path),
                image_path,
                str(out_dir),
                json.dumps(driver_settings),
            ],
            cwd=self.REPO_DIR,
            hf_home=os.environ["HF_HOME"],
            timeout=5400,
        )

        mesh_path = find_generated_mesh(str(out_dir), (".glb", ".gltf", ".obj", ".ply"))
        textures = collect_sidecars(mesh_path)

        notes = self.base_notes()
        notes.update({
            "sam3dSeed": str(driver_settings["seed"]),
            "sam3dSolidInterior": str(driver_settings["solid_interior"]).lower(),
            "sam3dSolidInteriorOpening": str(driver_settings["solid_interior_opening"]),
            "sam3dDumpField": str(driver_settings["dump_field"]).lower(),
            "sam3dMaskSource": (
                "none"
                if str(setting(params, "BACKGROUND_REMOVAL", "rembg")).lower() == "none"
                else "backgroundRemovalAlpha"
            ),
            "generatedMesh": Path(mesh_path).name,
        })

        # The driver's measurements ride along as a diagnostic and, compacted, as a note; a run that
        # produced a mesh but no measurements is still a run.
        diagnostics = []
        stats_path = out_dir / STATS_FILENAME
        try:
            stats = json.loads(stats_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            logger.warning(f"SAM 3D Objects wrote no {STATS_FILENAME} beside the mesh; the run's "
                           f"measurements are not recorded")
            notes["sam3dStats"] = "missing"
        except ValueError as e:
            logger.warning(f"SAM 3D Objects wrote an unreadable {STATS_FILENAME}: {e}")
            notes["sam3dStats"] = "unreadable"
            diagnostics.append(str(stats_path))
        else:
            notes["sam3dStats"] = json.dumps(stats, separators=(",", ":"))
            diagnostics.append(str(stats_path))
            fill = stats.get("solidInterior") if isinstance(stats, dict) else None
            if isinstance(fill, dict):
                notes["sam3dSolidInteriorRadius"] = str(fill.get("opening"))
                opening_flipped = fill.get("openingFlippedVertices")
                if opening_flipped == 0 and fill.get("openingRequested") == "auto":
                    by_radius = fill.get("flippedByRadius") or [0]
                    logger.warning(f"The interior fill flipped nothing: no radius up to {len(by_radius) - 1} "
                                   "flipped enough of the active vertices in one step to count as a seal "
                                   f"(flipped vertices by radius {by_radius}), so the search kept radius 0, "
                                   "where the flood from the grid boundary reached every positive vertex, "
                                   "and the mesh is the double wall the setting exists to avoid")
                elif opening_flipped == 0:
                    logger.warning("The interior fill flipped nothing: the flood from the grid boundary "
                                   "reached every positive vertex, so the surface has a puncture wider than "
                                   "twice the opening and the mesh is the double wall the setting exists to "
                                   "avoid; set SAM3D_SOLID_INTERIOR_OPENING to auto or raise it")
                elif fill.get("flippedVertices") == 0:
                    logger.warning(f"The interior fill flipped nothing: all {opening_flipped} vertices the "
                                   "opening sealed off lay outside the surface's outer skin or in pieces "
                                   "touching the outside, and were handed back, so the mesh is the double "
                                   "wall the setting exists to avoid")

        # The vertex grid the extractor seeded, when the run asked for it. Binary and tens of megabytes,
        # so it rides as a bulky diagnostic onto the auxiliary prefix.
        fields = sorted(out_dir.glob(Path(FIELD_FILENAME).stem + "*.npz"))
        diagnostics.extend(str(field) for field in fields)
        if driver_settings["dump_field"] and not fields:
            logger.warning(f"SAM3D_DUMP_FIELD is on but the driver wrote no {FIELD_FILENAME}; the mesh "
                           f"extraction never reached the recorded seeding")

        logger.info(f"SAM 3D Objects generated {mesh_path} with {len(textures)} texture sidecars")
        return GenerationResult(mesh_path=mesh_path, texture_paths=textures, notes=notes,
                                diagnostics=diagnostics)
