# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Per-part bounds of a merged scene, the parts the camera leaves out of its framing, and the environment
parts the preview leaves out.

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

A loader whose parts carry a reliable winding asks, before it merges them, which parts are environment
geometry enclosing the model (``environment_kept``): a room or environment box, or a sky sphere or
dome, authored to be seen from inside, so single-sided and facing the model. Those parts are not
loaded, and the model is loaded, framed, lit and drawn as from a file holding it alone. A shell that
faces outward, has a wall thickness, is double-sided or is in any other doubt is drawn as before.
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


# Environment geometry that encloses the model: a room or environment box, a sky sphere or dome, authored
# to be seen from inside. Rendered whole, the camera stands outside it and the preview shows its outside.
#
# A part is a shell part when its bounds span at least SHELL_SPAN of the scene's on two or more axes. The
# content is the union of the parts that are neither shell parts nor near-flat sheets, grown by the other
# parts that lie within NEAR_CONTENT of its largest extent of it; every other part is environment. The
# content is at most 1 / ENCLOSURE_SCALE of the scene on every axis and keeps a gap of ENCLOSURE_MARGIN of
# the scene's extent to the scene's bounds on every side but at most one (the floor it stands on).
SHELL_SPAN = 0.5
ENCLOSURE_SCALE = 8.0
ENCLOSURE_MARGIN = 0.05
NEAR_CONTENT = 0.25
# The positive evidence that the environment is meant to be seen from inside: every environment part is
# single-sided (its loader reads a reliable winding and no double-sided flag) and at least INWARD_SHARE of
# its area faces the content's centre. A shell that is part of a product faces outward: a single surface
# (a vase, a globe, a car body) has almost none of its area facing in, and a solid with a wall thickness
# (a housing, a container) about half. An environment box or sky authored to be seen from inside faces in
# with all of it, so the threshold leaves room for a few stray faces and stays well clear of one half.
INWARD_SHARE = 0.9
# Of ENCLOSURE_RAYS rays cast from the content's centre in evenly spread directions, at least
# ENCLOSURE_COVERAGE meet the environment (a ray leaving through the face the content stands on counts):
# a closed or near-closed shell, not an inward-facing backdrop open to the camera.
ENCLOSURE_RAYS = 64
ENCLOSURE_COVERAGE = 0.9
# Environment triangles the orientation and ray tests look at; a larger environment is left as it is,
# so the cost is bounded whatever the size of the input.
MAX_ENVIRONMENT_TRIANGLES = 1_000_000
# Triangles intersected with all the rays at once, to bound the temporary arrays.
_RAY_CHUNK = 8_192


class Part:
    """One part of a scene for the enclosure test: its vertices and triangles in world space, whether its
    loader read a reliable single-sided winding (False for a double-sided or unknown part), and whether
    its front faces wind clockwise (a USD leftHanded orientation or a mirroring transform)."""

    __slots__ = ("vertices", "triangles", "one_sided", "reversed_winding", "lo", "hi")

    def __init__(self, vertices, triangles, one_sided, reversed_winding=False):
        self.vertices = np.asarray(vertices, dtype=np.float64).reshape(-1, 3)
        self.triangles = np.asarray(triangles, dtype=np.int64).reshape(-1, 3)
        self.one_sided = bool(one_sided)
        self.reversed_winding = bool(reversed_winding)
        if len(self.vertices):
            self.lo, self.hi = self.vertices.min(axis=0), self.vertices.max(axis=0)
        else:
            self.lo = self.hi = np.zeros(3)


def _face_gaps(lo, hi, scene_lo, scene_hi):
    """The gap between a box and the scene's bounds on each face, in the order of a bounds row:
    (xmin, ymin, zmin, xmax, ymax, zmax)."""
    return np.concatenate([lo - scene_lo, scene_hi - hi])


def enclosure_screen(bounds):
    """The bounds-only screen for an enclosure: (lo, hi, kept, contact) when the part ``bounds`` show
    content surrounded by shell parts, else None. ``kept`` marks the content's parts (and the parts near
    it), (lo, hi) are their bounds and ``contact`` is the one face (bounds-row order) on which the content
    may touch the scene's bounds, or -1. Costs a few passes over the parts."""
    bounds = np.asarray(bounds, dtype=np.float64)
    if len(bounds) < 2:
        return None
    scene_lo, scene_hi = bounds[:, :3].min(axis=0), bounds[:, 3:].max(axis=0)
    scene_extent = scene_hi - scene_lo
    if not np.all(scene_extent > 0):
        return None
    raw = bounds[:, 3:] - bounds[:, :3]
    ordered = np.sort(raw, axis=1)
    largest = ordered[:, 2]
    flat = (largest > 0) & (ordered[:, 0] <= FLAT_RATIO * largest) & (ordered[:, 1] >= SHEET_RATIO * largest)
    shell = (raw >= SHELL_SPAN * scene_extent).sum(axis=1) >= 2
    content = ~shell & ~flat & (largest > 0)
    if not shell.any() or not content.any():
        return None
    lo, hi = bounds[content, :3].min(axis=0), bounds[content, 3:].max(axis=0)
    reach = NEAR_CONTENT * float((hi - lo).max())
    near = (~shell & ~content & np.all(bounds[:, :3] >= lo - reach, axis=1)
            & np.all(bounds[:, 3:] <= hi + reach, axis=1))
    if near.any():
        lo = np.minimum(lo, bounds[near, :3].min(axis=0))
        hi = np.maximum(hi, bounds[near, 3:].max(axis=0))
    if not np.all(scene_extent >= ENCLOSURE_SCALE * (hi - lo)):
        return None
    short = np.flatnonzero(_face_gaps(lo, hi, scene_lo, scene_hi) < ENCLOSURE_MARGIN * np.tile(scene_extent, 2))
    if len(short) > 1:
        return None
    return lo, hi, content | near, int(short[0]) if len(short) else -1


def _inward_share(part, centre):
    """The share of the part's area whose faces point toward ``centre``."""
    corners = part.vertices[part.triangles]
    normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    area = np.linalg.norm(normals, axis=1)
    total = float(area.sum())
    if total <= 0:
        return 0.0
    facing = np.einsum("ij,ij->i", normals, centre - corners.mean(axis=1)) > 0
    if part.reversed_winding:
        facing = ~facing
    return float(area[facing].sum()) / total


def _directions(n):
    """``n`` unit vectors spread evenly over the sphere (a Fibonacci lattice)."""
    i = np.arange(n) + 0.5
    z = 1.0 - 2.0 * i / n
    r = np.sqrt(1.0 - z * z)
    phi = np.pi * (1.0 + np.sqrt(5.0)) * i
    return np.column_stack([r * np.cos(phi), r * np.sin(phi), z])


def _ray_coverage(environment, lo, hi, contact):
    """The share of ``ENCLOSURE_RAYS`` rays from the centre of the box (lo, hi), started on the box's
    surface, that meet a triangle of the ``environment`` parts or leave the box through its ``contact``
    face. Moeller-Trumbore over the triangles, a chunk at a time."""
    eps = 1e-3 * float((hi - lo).max())
    lo, hi, centre = lo - eps, hi + eps, (lo + hi) / 2.0
    directions = _directions(ENCLOSURE_RAYS)
    with np.errstate(divide="ignore", invalid="ignore"):
        t_axes = np.where(directions > 0, (hi - centre) / directions,
                          np.where(directions < 0, (lo - centre) / directions, np.inf))
    axis = np.argmin(t_axes, axis=1)
    faces = axis + np.where(directions[np.arange(len(axis)), axis] > 0, 3, 0)
    origins = centre + directions * t_axes[np.arange(len(axis)), axis][:, None]
    hit = faces == contact
    for part in environment:
        corners = part.vertices[part.triangles]
        for start in range(0, len(corners), _RAY_CHUNK):
            if hit.all():
                return 1.0
            v0, v1, v2 = (corners[start:start + _RAY_CHUNK, k] for k in range(3))
            e1, e2 = v1 - v0, v2 - v0
            todo = np.flatnonzero(~hit)
            d, o = directions[todo][:, None, :], origins[todo][:, None, :]
            p = np.cross(d, e2[None])
            det = np.einsum("rtk,tk->rt", p, e1)
            with np.errstate(divide="ignore", invalid="ignore"):
                inv = 1.0 / det
                s = o - v0[None]
                u = np.einsum("rtk,rtk->rt", s, p) * inv
                q = np.cross(s, e1[None])
                v = np.einsum("rtk,rtk->rt", np.broadcast_to(d, q.shape), q) * inv
                t = np.einsum("rtk,tk->rt", q, e2) * inv
            meets = (np.abs(det) > 1e-12) & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 0)
            hit[todo[meets.any(axis=1)]] = True
    return float(hit.mean())


def _extents(values):
    return "({:.2f}, {:.2f}, {:.2f})".format(*values)


def _peel(parts, active):
    """(the indices of the environment parts of ``parts[active]``, the log line) when they enclose the
    content and every check passes, else None, with the reason a check failed logged at DEBUG."""
    index = np.flatnonzero(active)
    bounds = np.array([np.concatenate([parts[i].lo, parts[i].hi]) for i in index])
    screen = enclosure_screen(bounds)
    if screen is None:
        return None
    lo, hi, kept, contact = screen
    environment = [parts[i] for i in index[~kept]]
    if not all(part.one_sided for part in environment):
        logger.debug("Enclosure screen passed, but the environment is double-sided or its winding unknown")
        return None
    if sum(len(part.triangles) for part in environment) > MAX_ENVIRONMENT_TRIANGLES:
        logger.debug(f"Enclosure screen passed, but the environment holds over "
                     f"{MAX_ENVIRONMENT_TRIANGLES:,} triangles")
        return None
    eps = 1e-3 * float((hi - lo).max())
    for part in environment:
        if np.any(np.all((part.vertices > lo + eps) & (part.vertices < hi - eps), axis=1)):
            logger.debug("Enclosure screen passed, but the environment reaches into the content")
            return None
    centre = (lo + hi) / 2.0
    shares = [_inward_share(part, centre) for part in environment]
    if min(shares) < INWARD_SHARE:
        logger.debug(f"Enclosure screen passed, but an environment part faces the content with "
                     f"{min(shares):.0%} of its area")
        return None
    coverage = _ray_coverage(environment, lo, hi, contact)
    if coverage < ENCLOSURE_COVERAGE:
        logger.debug(f"Enclosure screen passed, but rays from the content meet it in {coverage:.0%} of directions")
        return None
    scene = bounds[:, 3:].max(axis=0) - bounds[:, :3].min(axis=0)
    message = (f"Enclosure left out of the preview: {len(environment)} of {len(index)} parts facing inward "
               f"(at least {min(shares):.0%} of their area), scene extents {_extents(scene)}, around content "
               f"of extents {_extents(hi - lo)}")
    return index[~kept], message


def environment_kept(parts):
    """A boolean mask over ``parts`` (``Part``): True for a part the preview keeps, or None to keep every
    part. Each layer of environment that encloses the content is left out, the outermost first, while
    every check holds for that layer: the bounds screen (``enclosure_screen``), single-sided parts, no
    environment vertex inside the content's bounds, at least ``INWARD_SHARE`` of each environment part's
    area facing the content, and at least ``ENCLOSURE_COVERAGE`` of the rays from the content meeting it.
    A layer that fails any check is drawn as before, and so is the whole scene when such a layer still
    encloses the content once the layers around it are left out: the preview would show the outside of
    that layer instead, which hides the model just as well."""
    if len(parts) < 2:
        return None
    active = np.ones(len(parts), dtype=bool)
    messages = []
    while active.sum() >= 2:
        peeled = _peel(parts, active)
        if peeled is None:
            break
        active[peeled[0]] = False
        messages.append(peeled[1])
    if active.all():
        return None
    if active.sum() >= 2 and enclosure_screen(np.array(
            [np.concatenate([parts[i].lo, parts[i].hi]) for i in np.flatnonzero(active)])) is not None:
        logger.info("Environment around the content not left out: a shell inside it still encloses the content "
                    "and is not one the preview leaves out")
        return None
    for message in messages:
        logger.info(message)
    return active
