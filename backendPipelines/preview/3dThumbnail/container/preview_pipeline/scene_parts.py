# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Per-part bounds of a merged scene, and the parts the camera leaves out of its framing.

A loader that merges several parts (USD prims, glTF/OBJ scene geometries, FBX meshes, STEP parts,
3MF/COLLADA instances) into one PolyData records each part's axis-aligned bounds in the field data
(``record_parts``), from the point counts of the parts in merge order. The bounds travel with the
data: an up-axis rotation transforms them (``rotate_z_up_to_y_up``) and the triangle budget carries
them to the decimated mesh (``carry_parts``). A loader that does not record parts, and a scene of one
part, frame exactly as before.

The camera leaves out of its framing a part that is a backdrop (``backdrop_parts``): a near-flat part,
such as a ground or environment plane, far larger than the rest of the scene. The part is still drawn.
"""

import numpy as np

from .utils.logging import get_logger

logger = get_logger()

# Field-data array holding one row per part: (xmin, ymin, zmin, xmax, ymax, zmax).
PART_BOUNDS = "vamsPreviewPartBounds"

# A part is a near-flat sheet when its smallest extent is at most FLAT_RATIO of its largest and its
# middle extent at least SHEET_RATIO of its largest: a plane, not a rod or a cable. A circuit board
# (1.6 mm on 100 mm) is not near-flat; a ground plane, often of zero thickness, is.
FLAT_RATIO = 0.005
SHEET_RATIO = 0.1
# A near-flat sheet is a backdrop when its largest extent is at least this many times the largest
# extent of the union of the scene's parts that are not near-flat sheets.
BACKDROP_SCALE = 20.0


def record_parts(pv_data, point_counts):
    """Record on ``pv_data`` the bounds of the parts it was merged from, given each part's point count
    in merge order. Nothing is recorded for fewer than two parts or when the counts do not add up to
    the data's points. Costs one min and one max over the points, as framing on the full bounds does."""
    counts = np.asarray([int(c) for c in point_counts if int(c) > 0], dtype=np.int64)
    if len(counts) < 2 or int(counts.sum()) != int(pv_data.n_points):
        return pv_data
    points = np.asarray(pv_data.points)
    starts = np.concatenate(([0], np.cumsum(counts)[:-1]))
    lo = np.minimum.reduceat(points, starts, axis=0)
    hi = np.maximum.reduceat(points, starts, axis=0)
    pv_data.field_data[PART_BOUNDS] = np.hstack([lo, hi]).astype(np.float64)
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
    """A boolean mask over the parts in ``bounds``: True for a backdrop, a near-flat sheet whose largest
    extent is at least ``BACKDROP_SCALE`` times that of the union of the parts that are not near-flat
    sheets. Points and zero-size parts are never backdrops and do not count as the rest of the scene.

    A scene made only of near-flat sheets (a sign, a floor plan) has no backdrop, and the mask never
    selects every part."""
    bounds = np.asarray(bounds, dtype=np.float64)
    mask = np.zeros(len(bounds), dtype=bool)
    if len(bounds) < 2:
        return mask
    extents = np.sort(bounds[:, 3:] - bounds[:, :3], axis=1)
    largest = extents[:, 2]
    flat = (largest > 0) & (extents[:, 0] <= FLAT_RATIO * largest) & (extents[:, 1] >= SHEET_RATIO * largest)
    solid = ~flat & (largest > 0)
    if not solid.any() or not flat.any():
        return mask
    rest = bounds[solid]
    rest_largest = float((rest[:, 3:].max(axis=0) - rest[:, :3].min(axis=0)).max())
    if rest_largest <= 0:
        return mask
    mask = flat & (largest >= BACKDROP_SCALE * rest_largest)
    if mask.all():
        return np.zeros(len(bounds), dtype=bool)
    return mask


def framing_bounds(pv_data):
    """The (lo, hi) the camera frames when backdrop parts are left out, or None to frame the whole
    data. Logs the parts left out."""
    bounds = part_bounds(pv_data)
    if bounds is None:
        return None
    mask = backdrop_parts(bounds)
    if not mask.any():
        return None
    kept = bounds[~mask]
    lo, hi = kept[:, :3].min(axis=0), kept[:, 3:].max(axis=0)
    left_out = bounds[mask]
    extents = ", ".join(
        "({:.2f}, {:.2f}, {:.2f})".format(*(b[3:] - b[:3])) for b in left_out[:5])
    more = f" and {len(left_out) - 5} more" if len(left_out) > 5 else ""
    logger.info(f"Camera framing leaves out {len(left_out)} of {len(bounds)} parts as a backdrop "
                f"(near-flat, at least {BACKDROP_SCALE:g}x the rest of the scene); extents {extents}{more}")
    return lo, hi
