# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Per-part bounds of a merged scene, and the parts the camera leaves out of its framing.

A loader that merges several parts (USD prims, glTF/GLB scene geometries, OBJ objects that trimesh
loads as separate geometries, FBX meshes, STEP parts, 3MF/COLLADA instances) into one PolyData
records each part's axis-aligned bounds in the field data (``record_parts``), from the point counts
of the parts in merge order. The bounds travel with the data: an up-axis rotation transforms them
(``rotate_z_up_to_y_up``) and the triangle budget carries them to the decimated mesh
(``carry_parts``). A loader that does not record parts, a scene of one part and a scene of more
parts than ``part_limit`` frame exactly as before.

A part that is a backdrop (``backdrop_parts``) -- a near-flat sheet, such as a ground or environment
plane, far larger than the rest of the scene and centred on it -- is left out of the render: the
loader drops it before it merges the parts (``backdrop_keep``), so it is neither drawn nor framed.
The preview has a transparent background and its own lights, so a backdrop adds nothing to it, and
drawn it filled the whole frame. ``framing_bounds`` leaves a backdrop out of the framing of data
whose loader recorded its parts without dropping it.
"""

import numpy as np

from . import resources
from .utils.logging import get_logger

logger = get_logger()

# Field-data arrays holding one row per part: (xmin, ymin, zmin, xmax, ymax, zmax), and its points.
PART_BOUNDS = "vamsPreviewPartBounds"
PART_POINTS = "vamsPreviewPartPoints"
# Set when the loader already dropped the scene's backdrops (``backdrop_keep``): the framing then does
# not judge the parts kept as backdrops again (a model's own platform, kept under a ground plane).
BACKDROPS_DROPPED = "vamsPreviewBackdropsDropped"

# A part is a near-flat sheet when its smallest extent is at most FLAT_RATIO of its largest and its
# middle extent at least SHEET_RATIO of its largest: a plane, not a rod or a cable. A circuit board
# (1.6 mm on 100 mm) is not near-flat; a ground plane, often of zero thickness, is.
FLAT_RATIO = 0.005
SHEET_RATIO = 0.1
# A near-flat sheet is a backdrop when its largest extent is at least BACKDROP_SCALE times the largest
# extent of the union of the scene's other parts (the content), and the content's centre lies within
# CENTRE_SHARE of the sheet's extent from the sheet's centre on each of the sheet's two large axes:
# the content stands in the middle of the sheet, as a robot on its ground plane or a model inside a
# skybox does. A small part in the corner of a large board, or a small object among floor tiles of
# which none is centred on it, leaves every sheet in the framing.
BACKDROP_SCALE = 50.0
CENTRE_SHARE = 0.25
# A backdrop sheet lying on a larger backdrop sheet (a model's own platform on a ground plane) is at
# most this share of the larger one's size; it is kept as part of the model.
NESTED_SHARE = 0.5

# A stray part is one the framing leaves out because it would shrink the model to nothing: a small
# piece left far from the rest (an export leftover, a helper object, a light's proxy). The core of the
# scene is its largest parts by points, taken in descending order until they hold at least
# 1 - STRAY_SHARE of the points; a part outside the core is a stray when framing the core with it
# would make the framed extent at least STRAY_SCALE times the core's own. At ten times the model fills
# a tenth of the frame; an exploded view or a scene of separate objects does not spread 5% of its
# points that far. A stray is still drawn. A near-flat sheet is never a stray: whether a sheet is left
# out is decided by the backdrop rule alone, at BACKDROP_SCALE.
STRAY_SHARE = 0.05
STRAY_SCALE = 10.0

# Parts tracked per scene. A backdrop is one part, or a few, at least BACKDROP_SCALE times the rest of
# the scene, so a scene of more parts (a point instancer's forest, a large assembly) frames as before.
# Memory charged per tracked part while the bounds are built: the six float64 bounds, the int64 point
# count kept, and the int64 count and start offset of the build; at most PART_MEMORY_FRACTION of the
# task's memory.
MAX_PARTS = 100_000
BYTES_PER_PART = 72
PART_MEMORY_FRACTION = 0.01


def part_limit(memory_bytes: int) -> int:
    """The most parts whose bounds are tracked in a task with ``memory_bytes`` of memory."""
    return max(2, min(MAX_PARTS, int(memory_bytes * PART_MEMORY_FRACTION / BYTES_PER_PART)))


def record_parts(pv_data, point_counts, max_parts: int = None, backdrops_dropped: bool = False):
    """Record on ``pv_data`` the bounds of the parts it was merged from, given each part's point count
    in merge order; ``backdrops_dropped`` says the loader already ran ``backdrop_keep`` on them.
    Nothing is recorded for fewer than two parts, for more than ``max_parts`` (default:
    ``part_limit`` of the running container's memory), or when the counts do not add up to the data's
    points. Costs one min and one max over the points, as framing on the full bounds does."""
    counts = np.asarray(point_counts, dtype=np.int64).reshape(-1)
    counts = counts[counts > 0]
    if len(counts) < 2 or int(counts.sum()) != int(pv_data.n_points):
        return pv_data
    if max_parts is None:
        max_parts = part_limit(resources.memory_limit_bytes())
    if len(counts) > max_parts:
        logger.info(f"Scene of {len(counts):,} parts: over the {max_parts:,} whose bounds are tracked; "
                    f"framed on the bounds of the whole scene")
        return pv_data
    points = np.asarray(pv_data.points)
    starts = np.concatenate(([0], np.cumsum(counts)[:-1]))
    bounds = np.empty((len(counts), 6), dtype=np.float64)
    np.minimum.reduceat(points, starts, axis=0, out=bounds[:, :3])
    np.maximum.reduceat(points, starts, axis=0, out=bounds[:, 3:])
    pv_data.field_data[PART_BOUNDS] = bounds
    pv_data.field_data[PART_POINTS] = counts
    if backdrops_dropped:
        pv_data.field_data[BACKDROPS_DROPPED] = np.array([1], dtype=np.int8)
    return pv_data


def part_points(pv_data):
    """The point count of each part recorded on ``pv_data`` (as loaded, before any decimation), or
    None."""
    bounds = part_bounds(pv_data)
    if bounds is None or PART_POINTS not in pv_data.field_data:
        return None
    counts = np.asarray(pv_data.field_data[PART_POINTS], dtype=np.int64).reshape(-1)
    return counts if len(counts) == len(bounds) else None


def part_bounds(pv_data):
    """The (n, 6) part bounds recorded on ``pv_data``, or None."""
    field_data = getattr(pv_data, "field_data", None)
    if field_data is None or PART_BOUNDS not in field_data:
        return None
    bounds = np.asarray(field_data[PART_BOUNDS], dtype=np.float64)
    if bounds.ndim != 2 or bounds.shape[1] != 6 or len(bounds) < 2 or not np.all(np.isfinite(bounds)):
        return None
    return bounds


def carry_parts(source, result):
    """Copy the part bounds of ``source`` onto ``result`` (a reduced copy of the same scene)."""
    bounds = part_bounds(source)
    if bounds is not None and result is not source:
        result.field_data[PART_BOUNDS] = bounds
        counts = part_points(source)
        if counts is not None:
            result.field_data[PART_POINTS] = counts
        if BACKDROPS_DROPPED in source.field_data:
            result.field_data[BACKDROPS_DROPPED] = source.field_data[BACKDROPS_DROPPED]
    return result


def rotate_z_up_to_y_up(pv_data):
    """Apply to the recorded part bounds the rotation (x, y, z) -> (x, z, -y) applied to the points."""
    bounds = part_bounds(pv_data)
    if bounds is not None:
        lo, hi = bounds[:, :3], bounds[:, 3:]
        pv_data.field_data[PART_BOUNDS] = np.column_stack(
            [lo[:, 0], lo[:, 2], -hi[:, 1], hi[:, 0], hi[:, 2], -lo[:, 1]])
    return pv_data


def backdrop_parts(bounds):
    """A boolean mask over the parts in ``bounds``: True for a backdrop. A backdrop is a near-flat sheet
    whose largest extent is at least ``BACKDROP_SCALE`` times that of the content, the union of the
    parts that are not near-flat sheets, and whose centre is within ``CENTRE_SHARE`` of its extent of
    the content's centre on the sheet's two large axes.

    A part of zero size (a single point) is neither a sheet nor content. A scene with no content (only
    sheets, such as a sign or a floor plan) has no backdrop, and a content part is never one, so the
    mask never selects every part."""
    bounds = np.asarray(bounds, dtype=np.float64)
    mask = np.zeros(len(bounds), dtype=bool)
    if len(bounds) < 2:
        return mask
    raw = bounds[:, 3:] - bounds[:, :3]
    order = np.argsort(raw, axis=1)
    extents = np.take_along_axis(raw, order, axis=1)
    largest = extents[:, 2]
    flat = (largest > 0) & (extents[:, 0] <= FLAT_RATIO * largest) & (extents[:, 1] >= SHEET_RATIO * largest)
    content = ~flat & (largest > 0)
    if not content.any() or not flat.any():
        return mask
    content_lo = bounds[content, :3].min(axis=0)
    content_hi = bounds[content, 3:].max(axis=0)
    content_largest = float((content_hi - content_lo).max())
    if content_largest <= 0:
        return mask
    # Offset of the content's centre from each sheet's centre, on the sheet's two large axes.
    offset = np.abs((content_lo + content_hi) / 2.0 - (bounds[:, :3] + bounds[:, 3:]) / 2.0)
    large_axes = order[:, 1:]
    centred = np.all(np.take_along_axis(offset, large_axes, axis=1)
                     <= CENTRE_SHARE * np.take_along_axis(raw, large_axes, axis=1), axis=1)
    selected = flat & centred & (largest >= BACKDROP_SCALE * content_largest)
    # Only the outermost sheet is a backdrop: a selected sheet lying on a larger selected sheet (the
    # platform a model stands on, on a ground plane) belongs to the model and is kept.
    for inner in np.flatnonzero(selected):
        for outer in np.flatnonzero(selected):
            if outer != inner and _lies_on(bounds, order, largest, inner, outer):
                mask[inner] = True
                break
    return selected & ~mask


def _lies_on(bounds, order, largest, inner, outer) -> bool:
    """True when sheet ``inner`` lies on sheet ``outer``: the same thin axis, at most half its size
    (NESTED_SHARE), and its footprint inside the outer sheet's on the two large axes. Six equal skybox
    walls do not lie on one another."""
    if order[inner, 0] != order[outer, 0] or largest[inner] > NESTED_SHARE * largest[outer]:
        return False
    axes = order[outer, 1:]
    return bool(np.all(bounds[inner, axes] >= bounds[outer, axes])
                and np.all(bounds[inner, axes + 3] <= bounds[outer, axes + 3]))


def _changing_backdrops(bounds):
    """The ``backdrop_parts`` mask of ``bounds`` when leaving the backdrops out changes the scene's
    bounds, else None: a backdrop that does not change them (an object at the centre of one of many
    floor tiles) is left as it is."""
    mask = backdrop_parts(bounds)
    if not mask.any():
        return None
    kept = bounds[~mask]
    lo, hi = kept[:, :3].min(axis=0), kept[:, 3:].max(axis=0)
    if np.array_equal(lo, bounds[:, :3].min(axis=0)) and np.array_equal(hi, bounds[:, 3:].max(axis=0)):
        return None
    return mask


def _describe(left_out) -> str:
    extents = ", ".join("({:.2f}, {:.2f}, {:.2f})".format(*(b[3:] - b[:3])) for b in left_out[:5])
    more = f" and {len(left_out) - 5} more" if len(left_out) > 5 else ""
    return f"extents {extents}{more}"


def backdrop_keep(part_points, max_parts: int = None):
    """Which parts of a scene to draw, given each part's points in merge order: a boolean mask over
    the parts, False for a backdrop, or None to draw every part.

    A loader calls this on its parts before it merges them (and before it bakes textures or fills
    colours across them), so a backdrop is not drawn and the parts it keeps load as a file holding
    them alone would. The rule is ``backdrop_parts``, with the guards of the framing: nothing is left
    out of a scene of one part, of more parts than ``max_parts`` (default: ``part_limit`` of the
    running container's memory), with no content part, or when leaving the backdrops out keeps the
    scene's bounds. A part without points is kept. Costs one min and one max over each part's
    points."""
    counts = [len(points) for points in part_points]
    present = [index for index, count in enumerate(counts) if count > 0]
    if len(present) < 2:
        return None
    if max_parts is None:
        max_parts = part_limit(resources.memory_limit_bytes())
    if len(present) > max_parts:
        return None
    bounds = np.full((len(counts), 6), np.nan, dtype=np.float64)
    for index in present:
        points = np.asarray(part_points[index])
        bounds[index, :3] = points.min(axis=0)[:3]
        bounds[index, 3:] = points.max(axis=0)[:3]
    return backdrop_keep_bounds(bounds, counts, max_parts)


def backdrop_keep_bounds(bounds, counts, max_parts: int = None):
    """``backdrop_keep`` from each part's (xmin, ymin, zmin, xmax, ymax, zmax) and point count, for a
    loader that tracks its parts' bounds as it builds them. A part of no points is kept."""
    bounds = np.asarray(bounds, dtype=np.float64).reshape(-1, 6)
    present = [index for index, count in enumerate(counts) if count > 0]
    if len(present) < 2:
        return None
    if max_parts is None:
        max_parts = part_limit(resources.memory_limit_bytes())
    if len(present) > max_parts:
        return None
    bounds = bounds[present]
    if not np.all(np.isfinite(bounds)):
        return None
    mask = _changing_backdrops(bounds)
    if mask is None:
        return None
    keep = np.ones(len(counts), dtype=bool)
    keep[np.asarray(present)[mask]] = False
    logger.info(f"Preview leaves out {int(mask.sum())} of {len(present)} parts as a backdrop, from "
                f"the render and the framing (near-flat, at least {BACKDROP_SCALE:g}x the rest of the "
                f"scene and centred on it); {_describe(bounds[mask])}")
    return keep


def _near_flat(bounds):
    """True per part for a near-flat sheet (``FLAT_RATIO``, ``SHEET_RATIO``)."""
    extents = np.sort(bounds[:, 3:] - bounds[:, :3], axis=1)
    largest = extents[:, 2]
    return (largest > 0) & (extents[:, 0] <= FLAT_RATIO * largest) & (extents[:, 1] >= SHEET_RATIO * largest)


def stray_parts(bounds, counts):
    """A boolean mask over the parts: True for a stray (``STRAY_SHARE``, ``STRAY_SCALE``), given each
    part's point count. A scene of one part, of no points, or whose core has no extent has none, and a
    core part or a near-flat sheet is never one, so the mask never selects every part."""
    bounds = np.asarray(bounds, dtype=np.float64)
    counts = np.asarray(counts, dtype=np.float64)
    mask = np.zeros(len(bounds), dtype=bool)
    total = counts.sum()
    if len(bounds) < 2 or total <= 0:
        return mask
    order = np.argsort(-counts, kind="stable")
    held = np.cumsum(counts[order])
    n_core = int(np.searchsorted(held, (1.0 - STRAY_SHARE) * total)) + 1
    core = np.zeros(len(bounds), dtype=bool)
    core[order[:n_core]] = True
    core_lo, core_hi = bounds[core, :3].min(axis=0), bounds[core, 3:].max(axis=0)
    core_largest = float((core_hi - core_lo).max())
    if core_largest <= 0:
        return mask
    with_part = np.maximum(core_hi, bounds[:, 3:]) - np.minimum(core_lo, bounds[:, :3])
    far = with_part.max(axis=1) >= STRAY_SCALE * core_largest
    return ~core & far & ~_near_flat(bounds)


def framing_bounds(pv_data):
    """The (lo, hi) the camera frames when backdrop and stray parts are left out, or None to frame the
    whole data (none, or leaving them out does not change the bounds). Logs the parts left out. A
    loader that records parts has already dropped its backdrops from the data (``backdrop_keep``), so
    the backdrop test here applies to data whose parts were recorded without that step. A stray part
    is still drawn."""
    bounds = part_bounds(pv_data)
    if bounds is None:
        return None
    backdrops = None if BACKDROPS_DROPPED in pv_data.field_data else _changing_backdrops(bounds)
    left_out = np.zeros(len(bounds), dtype=bool) if backdrops is None else backdrops.copy()
    strays = np.zeros(len(bounds), dtype=bool)
    counts = part_points(pv_data)
    rest = np.flatnonzero(~left_out)
    if counts is not None and len(rest) >= 2:
        strays[rest] = stray_parts(bounds[rest], counts[rest])
    left_out |= strays
    if not left_out.any():
        return None
    kept = bounds[~left_out]
    lo, hi = kept[:, :3].min(axis=0), kept[:, 3:].max(axis=0)
    if np.array_equal(lo, bounds[:, :3].min(axis=0)) and np.array_equal(hi, bounds[:, 3:].max(axis=0)):
        return None
    if backdrops is not None:
        logger.info(f"Camera framing leaves out {int(backdrops.sum())} of {len(bounds)} parts as a "
                    f"backdrop (near-flat, at least {BACKDROP_SCALE:g}x the rest of the scene and "
                    f"centred on it); {_describe(bounds[backdrops])}")
    if strays.any():
        logger.info(f"Camera framing leaves out {int(strays.sum())} of {len(bounds)} parts as stray "
                    f"(outside the parts holding {1 - STRAY_SHARE:.0%} of the points, and widening the "
                    f"frame at least {STRAY_SCALE:g}x); they are still drawn; {_describe(bounds[strays])}")
    return lo, hi
