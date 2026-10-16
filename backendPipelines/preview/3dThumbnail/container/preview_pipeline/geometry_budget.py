# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Bring loaded geometry within the run's render budgets before it reaches the CPU rasteriser.

* A point cloud above the point budget is sampled uniformly at random.
* A mesh above the triangle budget is decimated with VTK quadric clustering, which runs in linear
  time on any input size. Per-point colours travel as cell colours through the clustering; a UV
  texture is baked into per-point colours first, since the clustered mesh has no texture coordinates.
"""

import numpy as np

from .renderer import is_point_cloud
from .utils.logging import get_logger

logger = get_logger()

# Passes of quadric clustering allowed to land under the triangle budget.
_CLUSTER_PASSES = 4


def apply_budgets(pv_data, budgets):
    """``pv_data`` within ``budgets.points`` (point clouds) or ``budgets.triangles`` (meshes)."""
    if is_point_cloud(pv_data):
        return limit_points(pv_data, budgets.points)
    return limit_triangles(pv_data, budgets.triangles)


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


def limit_triangles(pv_data, max_triangles: int):
    """Decimate a mesh above ``max_triangles`` triangles with quadric clustering."""
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

    points = np.asarray(mesh.points, dtype=np.float64)
    faces = np.asarray(mesh.faces).reshape(-1, 4)[:, 1:]
    extent = np.maximum(points.max(axis=0) - points.min(axis=0), 1e-12)
    area = max(_surface_area(points, faces), 1e-12)
    # Each occupied cell of a surface yields about two triangles: aim at budget / 2 cells.
    spacing = np.sqrt(2.0 * area / max_triangles)
    result = None
    for attempt in range(_CLUSTER_PASSES):
        divisions = np.ceil(extent / spacing)
        result = _cluster(mesh, divisions)
        logger.info(f"Triangle budget pass {attempt + 1}: divisions {divisions.astype(int).tolist()} "
                    f"-> {result.n_cells:,} triangles (budget {max_triangles:,})")
        if result.n_cells <= max_triangles:
            break
        spacing *= np.sqrt(result.n_cells / max_triangles) * 1.05
    if "RGB" in result.cell_data:
        result.cell_data["RGB"] = np.asarray(result.cell_data["RGB"]).astype(np.uint8)
    logger.info(f"Triangle budget: decimated {n_faces:,} to {result.n_cells:,} triangles")
    return result
