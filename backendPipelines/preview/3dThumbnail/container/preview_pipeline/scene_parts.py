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

The camera leaves out of its framing a part that is a backdrop (``backdrop_parts``): a near-flat
sheet, such as a ground or environment plane, far larger than the rest of the scene and centred on
it. The part is still drawn.
"""

import numpy as np

from . import resources
from .utils.logging import get_logger

logger = get_logger()

# Field-data array holding one row per part: (xmin, ymin, zmin, xmax, ymax, zmax).
PART_BOUNDS = "vamsPreviewPartBounds"

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
BACKDROP_SCALE = 20.0
CENTRE_SHARE = 0.25

# Parts tracked per scene. A backdrop is one part, or a few, at least BACKDROP_SCALE times the rest of
# the scene, so a scene of more parts (a point instancer's forest, a large assembly) frames as before.
# Memory charged per tracked part while the bounds are built: the six float64 bounds, the int64 point
# count and its start offset; at most PART_MEMORY_FRACTION of the task's memory.
MAX_PARTS = 100_000
BYTES_PER_PART = 64
PART_MEMORY_FRACTION = 0.01


def part_limit(memory_bytes: int) -> int:
    """The most parts whose bounds are tracked in a task with ``memory_bytes`` of memory."""
    return max(2, min(MAX_PARTS, int(memory_bytes * PART_MEMORY_FRACTION / BYTES_PER_PART)))


def record_parts(pv_data, point_counts, max_parts: int = None):
    """Record on ``pv_data`` the bounds of the parts it was merged from, given each part's point count
    in merge order. Nothing is recorded for fewer than two parts, for more than ``max_parts`` (default:
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
    return pv_data


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
    return flat & centred & (largest >= BACKDROP_SCALE * content_largest)


def framing_bounds(pv_data):
    """The (lo, hi) the camera frames when backdrop parts are left out, or None to frame the whole
    data (no backdrop, or leaving the backdrops out does not change the bounds). Logs the parts left
    out."""
    bounds = part_bounds(pv_data)
    if bounds is None:
        return None
    mask = backdrop_parts(bounds)
    if not mask.any():
        return None
    kept = bounds[~mask]
    lo, hi = kept[:, :3].min(axis=0), kept[:, 3:].max(axis=0)
    if np.array_equal(lo, bounds[:, :3].min(axis=0)) and np.array_equal(hi, bounds[:, 3:].max(axis=0)):
        return None
    left_out = bounds[mask]
    extents = ", ".join(
        "({:.2f}, {:.2f}, {:.2f})".format(*(b[3:] - b[:3])) for b in left_out[:5])
    more = f" and {len(left_out) - 5} more" if len(left_out) > 5 else ""
    logger.info(f"Camera framing leaves out {len(left_out)} of {len(bounds)} parts as a backdrop "
                f"(near-flat, at least {BACKDROP_SCALE:g}x the rest of the scene and centred on it); "
                f"extents {extents}{more}")
    return lo, hi
