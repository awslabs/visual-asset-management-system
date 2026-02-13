# Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Mesh format handler for .ply, .stl, .obj, .glb, .fbx, .drc files.
Loads mesh geometry using trimesh and converts to PyVista for rendering.
"""

import os
import numpy as np
import trimesh
import pyvista as pv
from ..utils.logging import get_logger

logger = get_logger()

SUPPORTED_EXTENSIONS = {".ply", ".stl", ".obj", ".glb", ".gltf", ".fbx", ".drc"}


def can_handle(extension: str) -> bool:
    return extension.lower() in SUPPORTED_EXTENSIONS


def load(file_path: str) -> pv.PolyData:
    """
    Load a mesh file and return a PyVista PolyData object for rendering.
    Supports: .ply, .stl, .obj, .glb, .gltf, .fbx, .drc
    """
    ext = os.path.splitext(file_path)[1].lower()
    logger.info(f"Loading mesh file: {file_path} (format: {ext})")

    if ext == ".drc":
        return _load_draco(file_path)

    # Use trimesh for all other mesh formats
    try:
        scene_or_mesh = trimesh.load(file_path, force=None)
    except (NotImplementedError, Exception) as e:
        # trimesh doesn't support FBX natively; fall back to Open3D
        if ext == ".fbx":
            logger.warning(f"trimesh does not support FBX, falling back to Open3D: {e}")
            return _load_fbx_open3d(file_path)
        logger.error(f"Failed to load mesh with trimesh: {e}")
        raise

    # If trimesh returns a Scene (multi-object), merge all meshes
    if isinstance(scene_or_mesh, trimesh.Scene):
        trimeshes = []
        for name, geom in scene_or_mesh.geometry.items():
            if isinstance(geom, trimesh.Trimesh):
                trimeshes.append(geom)
            elif isinstance(geom, trimesh.PointCloud):
                return _pointcloud_to_pyvista(geom)
        if not trimeshes:
            raise ValueError("No valid mesh geometry found in scene")

        logger.info(f"Loaded scene with {len(trimeshes)} geometries")

        # Single geometry: preserve full UV texture for proper PBR rendering
        if len(trimeshes) == 1:
            return _trimesh_to_pyvista_textured(trimeshes[0])

        # Multiple geometries: bake textures to vertex colors then merge
        baked = [_bake_texture_to_vertex_colors(m) for m in trimeshes]
        merged = trimesh.util.concatenate(baked)
        return _trimesh_to_pyvista(merged)

    elif isinstance(scene_or_mesh, trimesh.Trimesh):
        return _trimesh_to_pyvista_textured(scene_or_mesh)
    elif isinstance(scene_or_mesh, trimesh.PointCloud):
        return _pointcloud_to_pyvista(scene_or_mesh)
    else:
        raise ValueError(f"Unsupported trimesh result type: {type(scene_or_mesh)}")



def _trimesh_to_pyvista_textured(mesh: trimesh.Trimesh) -> pv.PolyData:
    """
    Convert a trimesh.Trimesh to PyVista PolyData, preserving UV texture mapping
    when available. Falls back to vertex color baking if texture extraction fails.
    """
    vertices = np.array(mesh.vertices)
    faces = np.array(mesh.faces)
    n_faces = len(faces)
    pv_faces = np.column_stack([np.full(n_faces, 3), faces]).ravel()
    pv_mesh = pv.PolyData(vertices, pv_faces)

    # Try to extract UV-mapped texture for proper rendering
    if (hasattr(mesh, 'visual') and
            hasattr(mesh.visual, 'kind') and
            mesh.visual.kind == 'texture' and
            hasattr(mesh.visual, 'uv') and
            mesh.visual.uv is not None):
        try:
            mat = mesh.visual.material
            tex_image = getattr(mat, 'baseColorTexture', None) or getattr(mat, 'image', None)
            if tex_image is not None:
                # Set UV coordinates on mesh
                pv_mesh.active_texture_coordinates = np.array(mesh.visual.uv)
                # Create PyVista texture from the base color image
                pv_mesh._preview_texture = pv.Texture(np.array(tex_image))
                logger.info(f"Loaded textured mesh: {len(vertices)} vertices, {n_faces} faces, "
                            f"texture={tex_image.size}")
                return pv_mesh
        except Exception as e:
            logger.warning(f"Failed to extract UV texture, falling back to vertex colors: {e}")

    # Fallback: bake texture to vertex colors or use existing vertex colors
    mesh = _bake_texture_to_vertex_colors(mesh)
    if mesh.visual and hasattr(mesh.visual, 'vertex_colors') and mesh.visual.vertex_colors is not None:
        colors = np.array(mesh.visual.vertex_colors)
        if colors.ndim == 2 and colors.shape[1] >= 3 and colors.shape[0] == len(vertices):
            pv_mesh.point_data["RGB"] = colors[:, :3]
        elif colors.ndim == 2 and colors.shape[1] >= 3:
            logger.warning(f"Vertex color count ({colors.shape[0]}) does not match "
                           f"vertex count ({len(vertices)}), skipping vertex colors")

    logger.info(f"Loaded mesh: {len(vertices)} vertices, {n_faces} faces")
    return pv_mesh


def _bake_texture_to_vertex_colors(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """
    If the mesh has UV-mapped textures (common in GLB/GLTF), bake the texture
    to per-vertex colors so they survive the PyVista conversion.
    """
    try:
        if (hasattr(mesh, 'visual') and
                hasattr(mesh.visual, 'kind') and
                mesh.visual.kind == 'texture'):
            logger.info("Baking UV texture to vertex colors for PyVista rendering")
            mesh.visual = mesh.visual.to_color()
    except Exception as e:
        logger.warning(f"Failed to bake texture to vertex colors: {e}")
    return mesh


def _trimesh_to_pyvista(mesh: trimesh.Trimesh) -> pv.PolyData:
    """
    Convert a trimesh.Trimesh to PyVista PolyData.
    """
    vertices = np.array(mesh.vertices)
    faces = np.array(mesh.faces)

    # PyVista expects faces as [n_verts, v0, v1, v2, ...] format
    n_faces = len(faces)
    pv_faces = np.column_stack([np.full(n_faces, 3), faces]).ravel()

    pv_mesh = pv.PolyData(vertices, pv_faces)

    # Transfer vertex colors if available
    if mesh.visual and hasattr(mesh.visual, 'vertex_colors') and mesh.visual.vertex_colors is not None:
        colors = np.array(mesh.visual.vertex_colors)
        if colors.ndim == 2 and colors.shape[1] >= 3 and colors.shape[0] == len(vertices):
            # Use RGB only (drop alpha)
            pv_mesh.point_data["RGB"] = colors[:, :3]
        elif colors.ndim == 2 and colors.shape[1] >= 3:
            logger.warning(f"Vertex color count ({colors.shape[0]}) does not match "
                           f"vertex count ({len(vertices)}), skipping vertex colors")

    logger.info(f"Loaded mesh: {len(vertices)} vertices, {n_faces} faces")
    return pv_mesh


def _pointcloud_to_pyvista(pc: trimesh.PointCloud) -> pv.PolyData:
    """
    Convert a trimesh PointCloud to PyVista PolyData.
    """
    points = np.array(pc.vertices)
    pv_cloud = pv.PolyData(points)
    if pc.colors is not None and len(pc.colors) > 0:
        colors = np.array(pc.colors)
        if colors.shape[1] >= 3:
            pv_cloud.point_data["RGB"] = colors[:, :3]
    return pv_cloud


def _load_draco(file_path: str) -> pv.PolyData:
    """
    Load a Draco compressed mesh (.drc) using DracoPy.
    """
    try:
        import DracoPy
    except ImportError:
        raise ImportError("DracoPy is required for .drc file support")

    logger.info(f"Loading Draco file: {file_path}")
    with open(file_path, "rb") as f:
        data = f.read()

    mesh = DracoPy.decode(data)

    vertices = np.array(mesh.points).reshape(-1, 3)

    if mesh.faces is not None and len(mesh.faces) > 0:
        faces = np.array(mesh.faces).reshape(-1, 3)
        n_faces = len(faces)
        pv_faces = np.column_stack([np.full(n_faces, 3), faces]).ravel()
        pv_mesh = pv.PolyData(vertices, pv_faces)
    else:
        pv_mesh = pv.PolyData(vertices)

    logger.info(f"Loaded Draco mesh: {len(vertices)} vertices")
    return pv_mesh


def _load_fbx_open3d(file_path: str) -> pv.PolyData:
    """
    Load FBX file using Open3D as a fallback for trimesh.
    FBX is a proprietary Autodesk format not supported by trimesh.
    """
    try:
        import open3d as o3d
    except ImportError:
        raise ImportError("Open3D is required for .fbx file support")

    logger.info(f"Loading FBX file via Open3D: {file_path}")
    mesh = o3d.io.read_triangle_mesh(file_path)

    if mesh.is_empty():
        raise ValueError(f"Open3D could not load FBX file or file is empty: {file_path}")

    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    triangles = np.asarray(mesh.triangles, dtype=np.int64)

    n_faces = len(triangles)
    pv_faces = np.column_stack([np.full(n_faces, 3), triangles]).ravel()
    pv_mesh = pv.PolyData(vertices, pv_faces)

    # Transfer vertex colors if available
    if mesh.has_vertex_colors():
        colors = (np.asarray(mesh.vertex_colors) * 255).astype(np.uint8)
        pv_mesh.point_data["RGB"] = colors

    logger.info(f"Loaded FBX mesh via Open3D: {len(vertices)} vertices, {n_faces} faces")
    return pv_mesh
