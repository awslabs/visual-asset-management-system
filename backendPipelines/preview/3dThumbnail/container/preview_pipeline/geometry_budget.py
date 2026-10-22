# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Bring loaded geometry within the run's render budgets before it reaches the CPU rasteriser.

* A point cloud above the point budget is sampled uniformly at random.
* A mesh above the triangle budget is decimated with VTK quadric clustering, which runs in linear
  time on any input size. Per-point colours travel as cell colours through the clustering; a UV
  texture is baked into per-point colours first, since the clustered mesh has no texture coordinates.

VTK allocates the clustering grid densely, one 88-byte quadric per bin whether the bin is occupied or
not, and counts the bins in a C ``int``. A grid sized from the triangle budget alone grows with the
budget to the power 1.5: a sphere at a 20 million triangle budget asks for 1785 divisions per axis,
5.7 billion bins, which overflows the count and aborts the process in the allocation. The grid is
therefore also bounded by the task's memory (``cluster_bin_limit``); a bounded grid gives a coarser
result.
"""

import math

import numpy as np

from . import resources
from .renderer import is_point_cloud
from .utils.logging import get_logger

logger = get_logger()

# Passes of quadric clustering allowed to land under the triangle budget.
_CLUSTER_PASSES = 4

# Bytes one bin of the clustering grid holds (``vtkQuadricClustering::PointQuadric``: a vertex id, a
# dimension flag and a nine-term quadric), the share of the memory limit the grid may take (the mesh
# being decimated and its copies hold the rest), and a ceiling far below the 2^31 bins a C ``int``
# counts. 2^26 bins already resolve finer than an 800x600 thumbnail's pixels on every axis.
BYTES_PER_CLUSTER_BIN = 88
CLUSTER_MEMORY_FRACTION = 0.125
MAX_CLUSTER_BINS = 2 ** 26


def apply_budgets(pv_data, budgets):
    """``pv_data`` within ``budgets.points`` (point clouds) or ``budgets.triangles`` (meshes), a
    decimation grid within ``cluster_bin_limit(budgets.memory_bytes)``. An up axis the loader read
    from the file travels to the reduced data."""
    if is_point_cloud(pv_data):
        result = limit_points(pv_data, budgets.points)
    else:
        result = limit_triangles(pv_data, budgets.triangles,
                                 max_bins=cluster_bin_limit(budgets.memory_bytes))
    up_axis = getattr(pv_data, "_preview_up_axis", None)
    if result is not pv_data and isinstance(up_axis, str):
        result._preview_up_axis = up_axis
    return result


def limit_points(pv_data, max_points: int, seed: int = 42):
    import pyvista as pv

    if pv_data.n_points <= max_points:
        return pv_data
    keep = np.sort(np.random.default_rng(seed).choice(pv_data.n_points, size=max_points, replace=False))
    sampled = pv.PolyData(np.asarray(pv_data.points)[keep])
    for name in pv_data.point_data.keys():
        values = np.asarray(pv_data.point_data[name])
        if len(values) == pv_data.n_points:
            sampled.point_data[name] = values[keep]
    logger.info(f"Point budget: sampled {max_points:,} of {pv_data.n_points:,} points")
    return sampled


def _bake_texture(pv_data):
    """Per-point RGB sampled from the mesh's UV texture (nearest texel, wrapped)."""
    texture = getattr(pv_data, "_preview_texture", None)
    uv = pv_data.active_texture_coordinates
    if texture is None or uv is None:
        return None
    image = np.asarray(texture.to_array())
    if image.ndim == 2:
        image = np.repeat(image[..., None], 3, axis=-1)
    height, width = image.shape[:2]
    uv = np.asarray(uv, dtype=np.float64)
    # VTK textures have their origin at the bottom-left; the array's first row is the image top.
    columns = np.mod(np.floor(uv[:, 0] * width), width).astype(np.int64)
    rows = np.mod(np.floor((1.0 - uv[:, 1]) * height), height).astype(np.int64)
    return np.ascontiguousarray(image[rows, columns, :3], dtype=np.uint8)


def _surface_area(points: np.ndarray, faces: np.ndarray, chunk: int = 5_000_000) -> float:
    total = 0.0
    for start in range(0, len(faces), chunk):
        tri = points[faces[start:start + chunk]]
        total += 0.5 * float(np.linalg.norm(
            np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1).sum())
    return total


def cluster_bin_limit(memory_bytes: int) -> int:
    """The most bins a clustering grid may hold in a task with ``memory_bytes`` of memory."""
    by_memory = int(memory_bytes * CLUSTER_MEMORY_FRACTION / BYTES_PER_CLUSTER_BIN)
    return max(1, min(MAX_CLUSTER_BINS, by_memory))


def cluster_spacing(area: float, max_triangles: int) -> float:
    """The bin edge at which a surface of ``area`` clusters to about ``max_triangles`` triangles: each
    occupied bin yields about two triangles, so the grid aims at ``max_triangles / 2`` occupied bins."""
    return math.sqrt(2.0 * max(float(area), 1e-12) / max_triangles)


def grid_divisions(extent, spacing: float) -> list:
    """Per-axis divisions of a grid of bins ``spacing`` wide over ``extent`` (at least one per axis)."""
    return [max(1, int(math.ceil(max(float(e), 0.0) / spacing))) for e in extent]


def bounded_spacing(extent, spacing: float, max_bins: int) -> float:
    """``spacing``, or the smallest larger bin edge whose grid over ``extent`` holds at most
    ``max_bins`` bins. A grid as wide as the largest extent has one bin, so the search always ends
    within the limit."""
    if math.prod(grid_divisions(extent, spacing)) <= max_bins:
        return spacing
    low, high = spacing, max(spacing, max(float(e) for e in extent))
    for _ in range(64):
        middle = math.sqrt(low * high)
        if math.prod(grid_divisions(extent, middle)) <= max_bins:
            high = middle
        else:
            low = middle
    return high


def _cluster(polydata, divisions):
    import pyvista as pv
    import vtk

    clustering = vtk.vtkQuadricClustering()
    clustering.SetInputData(polydata)
    clustering.SetNumberOfDivisions(*[int(max(1, d)) for d in divisions])
    clustering.SetCopyCellData(True)
    clustering.SetAutoAdjustNumberOfDivisions(False)
    clustering.Update()
    return pv.wrap(clustering.GetOutput())


def limit_triangles(pv_data, max_triangles: int, max_bins: int = None):
    """Decimate a mesh above ``max_triangles`` triangles with quadric clustering on a grid of at most
    ``max_bins`` bins (default: ``cluster_bin_limit`` of the running container's memory)."""
    import pyvista as pv

    n_faces = int(pv_data.n_cells)
    if n_faces <= max_triangles:
        return pv_data
    if int(getattr(pv_data, "n_verts", 0) or 0):
        # A scene that mixes meshes and points: clustering would drop the vertex cells.
        logger.warning(f"Triangle budget: {n_faces:,} cells exceed {max_triangles:,}, but the scene "
                       f"mixes points and faces; rendering it undecimated")
        return pv_data

    source = pv_data if pv_data.is_all_triangles else pv_data.triangulate()
    mesh = pv.PolyData(np.asarray(source.points), np.asarray(source.faces))
    rgb = _bake_texture(pv_data)
    if rgb is None and "RGB" in pv_data.point_data and len(pv_data.point_data["RGB"]) == pv_data.n_points:
        rgb = np.asarray(pv_data.point_data["RGB"])[:, :3]
    if rgb is not None:
        mesh.point_data["RGB"] = rgb.astype(np.uint8)
        mesh = mesh.point_data_to_cell_data(pass_point_data=False)

    if max_bins is None:
        max_bins = cluster_bin_limit(resources.memory_limit_bytes())
    points = np.asarray(mesh.points, dtype=np.float64)
    faces = np.asarray(mesh.faces).reshape(-1, 4)[:, 1:]
    extent = np.maximum(points.max(axis=0) - points.min(axis=0), 1e-12)
    spacing = cluster_spacing(_surface_area(points, faces), max_triangles)
    result = None
    for attempt in range(_CLUSTER_PASSES):
        spacing = bounded_spacing(extent, spacing, max_bins)
        divisions = grid_divisions(extent, spacing)
        result = _cluster(mesh, divisions)
        logger.info(f"Triangle budget pass {attempt + 1}: divisions {divisions} "
                    f"({math.prod(divisions):,} bins, limit {max_bins:,}) -> {result.n_cells:,} triangles "
                    f"(budget {max_triangles:,})")
        if result.n_cells <= max_triangles:
            break
        spacing *= math.sqrt(result.n_cells / max_triangles) * 1.05
    if "RGB" in result.cell_data:
        result.cell_data["RGB"] = np.asarray(result.cell_data["RGB"]).astype(np.uint8)
    logger.info(f"Triangle budget: decimated {n_faces:,} to {result.n_cells:,} triangles")
    return result
