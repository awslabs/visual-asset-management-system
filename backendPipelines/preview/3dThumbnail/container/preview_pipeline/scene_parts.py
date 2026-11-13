# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Per-part bounds of a merged scene, the parts the camera leaves out of its framing, and the environment
geometry the preview leaves out.

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

The preview leaves out environment geometry that encloses the content (``without_enclosure``): a room
or environment box, a sky sphere or dome, with any light cards or pictures on its walls, around a much
smaller model. Framed on the whole scene, the camera stood outside the enclosure and showed its
outside. The renderer's background is transparent and its lights are its own, so the enclosure adds
nothing a preview needs: the loaded scene is cut down to the content, right after loading while the
recorded part point counts still match the points, and is then budgeted, framed, lit and drawn as if
the file held the model alone. Only a scene whose part bounds pass the screen (``enclosure_screen``)
is looked at further: no environment point may lie inside the content's bounds, the content must be
drawn in far finer detail than the environment, and rays cast from the content must meet the
environment in nearly every direction (a closed or near-closed shell, not an open backdrop or a
floor). Any doubt, such as a hollow product whose shell is the asset, leaves the scene as it is.
"""

import numpy as np

from . import resources
from .utils.logging import get_logger

logger = get_logger()

# Field-data arrays holding one row per part: (xmin, ymin, zmin, xmax, ymax, zmax), and the part's
# point count in merge order (valid until the triangle budget reduces the points).
PART_BOUNDS = "vamsPreviewPartBounds"
PART_POINTS = "vamsPreviewPartPoints"

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

# Enclosures. A part is a shell part when its bounds span at least SHELL_SPAN of the scene's bounds on
# two or more axes: a wall, a floor, a ceiling, a closed box or a sphere around the scene. The content
# is the union of the parts that are neither shell parts nor near-flat sheets (a light card or a
# picture on a wall is neither content nor shell). An enclosure surrounds content whose largest extent
# is at most 1 / ENCLOSURE_SCALE of the scene's, with a gap of at least ENCLOSURE_MARGIN of the scene's
# extent on every side but one (the floor the model stands on). The parts that are not shell parts and
# lie within NEAR_CONTENT of the content's largest extent of it are kept with it; every other part is
# environment.
SHELL_SPAN = 0.5
ENCLOSURE_SCALE = 8.0
ENCLOSURE_MARGIN = 0.05
NEAR_CONTENT = 0.25
# The content is drawn in much finer detail than the enclosure: its points per unit of its bounding
# box's surface are at least DETAIL_RATIO times those of the points outside it, over the scene's
# bounding box, and it holds at least CONTENT_POINT_SHARE of the scene's points. Environment geometry
# is coarse for its size, even a dense sky dome, while a hollow product (a housing around a small part)
# carries its detail in the shell, which is the asset.
DETAIL_RATIO = 10.0
CONTENT_POINT_SHARE = 0.1
# Of ENCLOSURE_RAYS rays cast from the content's centre in evenly spread directions, at least
# ENCLOSURE_COVERAGE meet geometry outside the content (a ray leaving through the floor the content
# stands on counts): a closed or near-closed shell. An open backdrop or a floor covers far less.
ENCLOSURE_RAYS = 64
ENCLOSURE_COVERAGE = 0.9


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
    pv_data.field_data[PART_POINTS] = counts
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


def _sheets(bounds):
    """Per part: the extents, the axes in increasing order of extent, the largest extent and whether
    the part is a near-flat sheet."""
    raw = bounds[:, 3:] - bounds[:, :3]
    order = np.argsort(raw, axis=1)
    extents = np.take_along_axis(raw, order, axis=1)
    largest = extents[:, 2]
    flat = (largest > 0) & (extents[:, 0] <= FLAT_RATIO * largest) & (extents[:, 1] >= SHEET_RATIO * largest)
    return raw, order, largest, flat


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
    raw, order, largest, flat = _sheets(bounds)
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


def _face_gaps(lo, hi, scene_lo, scene_hi):
    """The gap between a box and the scene's bounds on each face, in the order of a bounds row:
    (xmin, ymin, zmin, xmax, ymax, zmax)."""
    return np.concatenate([lo - scene_lo, scene_hi - hi])


def enclosure_screen(bounds):
    """The bounds-only screen for an enclosure: (lo, hi, kept, contact) when the part ``bounds`` show
    content surrounded by shell parts, else None.

    Shell parts have bounds spanning at least ``SHELL_SPAN`` of the scene's on two or more axes. The
    content is the union of the parts that are neither shell parts nor near-flat sheets, grown by the
    other non-shell parts that lie within ``NEAR_CONTENT`` of it; ``kept`` marks those parts and (lo, hi)
    are their bounds. Every other part is environment. The content's largest extent is at most
    1 / ``ENCLOSURE_SCALE`` of the scene's, and it keeps a gap of ``ENCLOSURE_MARGIN`` of the scene's
    extent to the scene's bounds on every face but at most one, ``contact`` (a face index in bounds-row
    order, or -1). A scene without volume, without a shell part or without content has no enclosure.
    Costs a few passes over the parts."""
    bounds = np.asarray(bounds, dtype=np.float64)
    if len(bounds) < 2:
        return None
    scene_lo, scene_hi = bounds[:, :3].min(axis=0), bounds[:, 3:].max(axis=0)
    scene_extent = scene_hi - scene_lo
    if not np.all(scene_extent > 0):
        return None
    raw, _, largest, flat = _sheets(bounds)
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
    content_largest = float((hi - lo).max())
    if content_largest <= 0 or scene_extent.max() < ENCLOSURE_SCALE * content_largest:
        return None
    short = np.flatnonzero(_face_gaps(lo, hi, scene_lo, scene_hi) < ENCLOSURE_MARGIN * np.tile(scene_extent, 2))
    if len(short) > 1:
        return None
    return lo, hi, content | near, int(short[0]) if len(short) else -1


def _directions(n):
    """``n`` unit vectors spread evenly over the sphere (a Fibonacci lattice)."""
    i = np.arange(n) + 0.5
    z = 1.0 - 2.0 * i / n
    r = np.sqrt(1.0 - z * z)
    phi = np.pi * (1.0 + np.sqrt(5.0)) * i
    return np.column_stack([r * np.cos(phi), r * np.sin(phi), z])


# vtkPolyData orders its cells, and so its cell data: vertices, lines, polygons, then strips.
_CELL_KINDS = (("verts", "GetVerts"), ("lines", "GetLines"), ("faces", "GetPolys"), ("strips", "GetStrips"))


def _subset(pv_data, point_keep):
    """A copy of ``pv_data`` holding only the points in ``point_keep`` and the cells all of whose points
    are kept, in their order, with their point, cell and field data and the loader's ``_preview_*``
    attributes (texture, up axis). The kept part bounds are set by the caller."""
    import pyvista as pv

    index = np.cumsum(point_keep, dtype=np.int64) - 1
    cells, cell_keep = {}, []
    for keyword, getter in _CELL_KINDS:
        cell_array = getattr(pv_data, getter)()
        n_cells = cell_array.GetNumberOfCells()
        if not n_cells:
            continue
        connectivity = np.asarray(pv.convert_array(cell_array.GetConnectivityArray()), dtype=np.int64)
        offsets = np.asarray(pv.convert_array(cell_array.GetOffsetsArray()), dtype=np.int64)
        sizes = np.diff(offsets)
        kept_corners = point_keep[connectivity]
        keep = np.ones(n_cells, dtype=bool)
        non_empty = sizes > 0
        keep[non_empty] = np.logical_and.reduceat(kept_corners, offsets[:-1][non_empty])
        cell_keep.append(keep)
        if keep.any():
            corners = np.repeat(keep, sizes)
            cells[keyword] = pv.CellArray.from_arrays(
                np.concatenate(([0], np.cumsum(sizes[keep]))), index[connectivity[corners]])
    subset = pv.PolyData(np.asarray(pv_data.points)[point_keep], **cells)
    point_data = pv_data.point_data
    for name in point_data.keys():
        subset.point_data[name] = np.asarray(point_data[name])[point_keep]
    for attribute in ("active_scalars_name", "active_normals_name", "active_texture_coordinates_name"):
        name = getattr(point_data, attribute, None)
        if name in subset.point_data:
            setattr(subset.point_data, attribute, name)
    keep_all = np.concatenate(cell_keep) if cell_keep else np.zeros(0, dtype=bool)
    for name in pv_data.cell_data.keys():
        subset.cell_data[name] = np.asarray(pv_data.cell_data[name])[keep_all]
    for name in pv_data.field_data.keys():
        if name not in (PART_BOUNDS, PART_POINTS):
            subset.field_data[name] = pv_data.field_data[name]
    for attribute, value in vars(pv_data).items():
        if attribute.startswith("_preview_"):
            setattr(subset, attribute, value)
    return subset


def _rays_meet(environment, lo, hi, contact, scene_extent) -> float:
    """The share of ``ENCLOSURE_RAYS`` rays from the centre of the box (lo, hi) that meet the
    ``environment`` mesh beyond the box, or leave the box through its ``contact`` face."""
    import vtk

    tree = vtk.vtkOBBTree()
    tree.SetDataSet(environment)
    tree.BuildLocator()
    eps = 1e-3 * float((hi - lo).max())
    lo, hi, centre = lo - eps, hi + eps, (lo + hi) / 2.0
    reach = 2.0 * float(np.linalg.norm(scene_extent))
    covered = 0
    for direction in _directions(ENCLOSURE_RAYS):
        with np.errstate(divide="ignore", invalid="ignore"):
            t_axes = np.where(direction > 0, (hi - centre) / direction,
                              np.where(direction < 0, (lo - centre) / direction, np.inf))
        axis = int(np.argmin(t_axes))
        if axis + (3 if direction[axis] > 0 else 0) == contact:
            covered += 1
            continue
        start = centre + direction * t_axes[axis]
        t, x, pcoords, sub_id, cell_id = vtk.reference(0.0), [0.0] * 3, [0.0] * 3, vtk.reference(0), vtk.reference(0)
        if tree.IntersectWithLine(tuple(start), tuple(start + direction * reach), 0.0, t, x, pcoords,
                                  sub_id, cell_id, vtk.vtkGenericCell()):
            covered += 1
    return covered / ENCLOSURE_RAYS


def _extents(rows):
    return "({:.2f}, {:.2f}, {:.2f})".format(*rows)


def _box_area(extent):
    x, y, z = (float(v) for v in extent)
    return 2.0 * (x * y + y * z + z * x)


def _finer(content_points, other_points, content_extent, scene_extent) -> bool:
    """True when the content's points per unit of its box's surface are at least ``DETAIL_RATIO`` times
    those of the other points over the scene's box."""
    content_area, scene_area = _box_area(content_extent), _box_area(scene_extent)
    if content_area <= 0 or scene_area <= 0:
        return False
    return content_points / content_area >= DETAIL_RATIO * other_points / scene_area


def without_enclosure(pv_data):
    """``pv_data`` without the environment geometry that encloses its content, or ``pv_data`` itself.

    Runs on a freshly loaded scene, while the recorded part point counts still match its points. The
    scene passes the bounds screen (``enclosure_screen``); no environment point lies inside the
    content's bounds; the content holds at least ``CONTENT_POINT_SHARE`` of the points and is drawn at
    least ``DETAIL_RATIO`` times finer than the environment; and at least ``ENCLOSURE_COVERAGE`` of the
    rays cast from the content meet the environment (a closed or near-closed shell). The result holds
    the content alone, as if the file held only the model, so it is framed, lit and drawn as an
    ordinary preview. Any doubt leaves the scene as it is. Only a scene that passes the screen pays for
    a pass over its points and a tree over the environment's cells, which is coarse by the detail
    test."""
    bounds = part_bounds(pv_data)
    if bounds is None or PART_POINTS not in pv_data.field_data:
        return pv_data
    counts = np.asarray(pv_data.field_data[PART_POINTS], dtype=np.int64)
    if len(counts) != len(bounds) or int(counts.sum()) != int(pv_data.n_points) or pv_data.n_cells == 0:
        return pv_data
    screen = enclosure_screen(bounds)
    if screen is None:
        return pv_data
    lo, hi, kept, contact = screen
    point_keep = np.repeat(kept, counts)
    environment_points = np.asarray(pv_data.points)[~point_keep]
    eps = 1e-3 * float((hi - lo).max())
    if np.any(np.all((environment_points > lo + eps) & (environment_points < hi - eps), axis=1)):
        logger.debug("Enclosure screen passed, but environment geometry reaches into the content")
        return pv_data
    content_points = int(point_keep.sum())
    scene_extent = bounds[:, 3:].max(axis=0) - bounds[:, :3].min(axis=0)
    if (content_points < CONTENT_POINT_SHARE * len(point_keep)
            or not _finer(content_points, len(point_keep) - content_points, hi - lo, scene_extent)):
        logger.debug(f"Enclosure screen passed, but the content holds {content_points:,} of "
                     f"{len(point_keep):,} points, too few or too coarse next to the environment")
        return pv_data
    environment = _subset(pv_data, ~point_keep)
    if environment.n_cells == 0:
        return pv_data
    coverage = _rays_meet(environment, lo, hi, contact, scene_extent)
    if coverage < ENCLOSURE_COVERAGE:
        logger.debug(f"Enclosure screen passed, but rays from the content meet it in {coverage:.0%} of directions")
        return pv_data
    content = _subset(pv_data, point_keep)
    if content.n_cells == 0:
        return pv_data
    if kept.sum() >= 2:
        content.field_data[PART_BOUNDS] = bounds[kept]
        content.field_data[PART_POINTS] = counts[kept]
    logger.info(f"Enclosure left out of the preview: {int((~kept).sum())} of {len(bounds)} parts, scene "
                f"extents {_extents(scene_extent)}, around content of extents {_extents(hi - lo)} "
                f"({content_points:,} of {len(point_keep):,} points)")
    return content
