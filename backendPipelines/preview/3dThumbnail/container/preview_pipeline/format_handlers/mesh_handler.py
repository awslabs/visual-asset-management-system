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
        # Use scene.dump() to get transformed meshes (applies scene graph
        # transforms so sub-meshes are positioned correctly relative to each other).
        trimeshes = []
        for geom in scene_or_mesh.dump():
            if isinstance(geom, trimesh.Trimesh):
                trimeshes.append(geom)
            elif isinstance(geom, trimesh.PointCloud):
                return _pointcloud_to_pyvista(geom)
        if not trimeshes:
            raise ValueError("No valid mesh geometry found in scene")

        logger.info(f"Loaded scene with {len(trimeshes)} geometries (transforms applied)")

        # Single geometry: preserve full UV texture for proper PBR rendering
        if len(trimeshes) == 1:
            return _trimesh_to_pyvista_textured(trimeshes[0])

        # Multiple geometries: bake textures to vertex colors then merge.
        # We must bake because PyVista can only apply one texture per mesh,
        # and multi-geometry scenes may have different textures per part.
        baked = []
        for m in trimeshes:
            b = _bake_texture_to_vertex_colors(m)
            # If baking failed and the mesh still has texture visual, force to vertex colors
            if hasattr(b, 'visual') and hasattr(b.visual, 'kind') and b.visual.kind == 'texture':
                try:
                    b.visual = b.visual.to_color()
                except Exception:
                    pass
            baked.append(b)
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
            # Try multiple ways to get the texture image from the material
            # Different trimesh versions and material types store it differently
            tex_image = None
            for attr_name in ['baseColorTexture', 'image', 'diffuseTexture']:
                tex_image = getattr(mat, attr_name, None)
                if tex_image is not None:
                    break

            # For PBR materials, try the baseColorTexture from the PBR properties
            if tex_image is None and hasattr(mat, 'pbrMetallicRoughness'):
                pbr = mat.pbrMetallicRoughness
                tex_image = getattr(pbr, 'baseColorTexture', None)

            # Try to get from material's main_color or diffuse property as PIL Image
            if tex_image is None and hasattr(mat, 'main_color'):
                # Some materials store a flat color, not a texture — skip these
                pass

            if tex_image is not None:
                # Set UV coordinates on mesh
                pv_mesh.active_texture_coordinates = np.array(mesh.visual.uv)
                # Create PyVista texture from the base color image
                pv_mesh._preview_texture = pv.Texture(np.array(tex_image))
                logger.info(f"Loaded textured mesh: {len(vertices)} vertices, {n_faces} faces, "
                            f"texture={tex_image.size}")
                return pv_mesh
            else:
                logger.info("No texture image found on material, falling back to vertex color baking")
        except Exception as e:
            logger.warning(f"Failed to extract UV texture, falling back to vertex colors: {e}")

    # Fallback: bake texture to vertex colors or use existing vertex colors
    mesh = _bake_texture_to_vertex_colors(mesh)
    if mesh.visual and hasattr(mesh.visual, 'vertex_colors') and mesh.visual.vertex_colors is not None:
        colors = np.array(mesh.visual.vertex_colors)

        # Handle single-color broadcast: to_color() may return (4,) or (3,) for flat-colored PBR materials
        if colors.ndim == 1 and len(colors) >= 3:
            # Broadcast single color to all vertices
            single_color = colors[:3].reshape(1, 3)
            pv_mesh.point_data["RGB"] = np.tile(single_color, (len(vertices), 1))
            logger.info(f"Broadcast single material color {colors[:3].tolist()} to all {len(vertices)} vertices")
        elif colors.ndim == 2 and colors.shape[1] >= 3 and colors.shape[0] == len(vertices):
            pv_mesh.point_data["RGB"] = colors[:, :3]
        elif colors.ndim == 2 and colors.shape[1] >= 3 and colors.shape[0] == 1:
            # Single row — broadcast
            pv_mesh.point_data["RGB"] = np.tile(colors[0, :3].reshape(1, 3), (len(vertices), 1))
            logger.info(f"Broadcast single material color to all {len(vertices)} vertices")
        elif colors.ndim == 2 and colors.shape[1] >= 3:
            logger.warning(f"Vertex color count ({colors.shape[0]}) does not match "
                           f"vertex count ({len(vertices)}), skipping vertex colors")
    else:
        # Last resort: check if the material has a baseColorFactor we can use directly
        bcf_color = _extract_material_color(mesh)
        if bcf_color is not None:
            single_color = bcf_color[:3].reshape(1, 3)
            pv_mesh.point_data["RGB"] = np.tile(single_color, (len(vertices), 1))
            logger.info(f"Applied material baseColorFactor {bcf_color[:3].tolist()} to all vertices")

    logger.info(f"Loaded mesh: {len(vertices)} vertices, {n_faces} faces")
    return pv_mesh


def _bake_texture_to_vertex_colors(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """
    If the mesh has UV-mapped textures or PBR material colors, convert to
    per-vertex ColorVisuals so colors survive PyVista conversion and merging.

    Handles several failure modes:
    - to_color() returns a single flat color instead of per-vertex colors
    - to_color() returns degenerate colors (all white/black) when a texture
      failed to sample, but a baseColorFactor exists on the material
    - to_color() throws an exception entirely
    """
    try:
        if (hasattr(mesh, 'visual') and
                hasattr(mesh.visual, 'kind') and
                mesh.visual.kind == 'texture'):
            logger.info("Baking texture/material to vertex colors for PyVista rendering")
            color_visual = mesh.visual.to_color()
            vc = np.array(color_visual.vertex_colors)

            # to_color() may return a single color (4,) for flat PBR materials.
            # In that case, manually create proper per-vertex colors.
            if vc.ndim == 1 and len(vc) >= 3:
                n_verts = len(mesh.vertices)
                full_colors = np.tile(vc[:4].reshape(1, -1), (n_verts, 1)).astype(np.uint8)
                mesh.visual = trimesh.visual.ColorVisuals(
                    mesh=mesh,
                    vertex_colors=full_colors,
                )
                logger.info(f"  Broadcast flat PBR color {vc[:3].tolist()} to {n_verts} vertices")
            else:
                # Check if to_color() returned degenerate all-same colors
                # (e.g. all white [255,255,255] or all black [0,0,0]) which
                # indicates the texture sampling failed silently.
                if vc.ndim == 2 and vc.shape[0] > 0:
                    rgb_mean = vc[:, :3].mean(axis=0)
                    rgb_std = vc[:, :3].std()
                    is_all_white = np.all(rgb_mean > 250) and rgb_std < 2.0
                    is_all_black = np.all(rgb_mean < 5) and rgb_std < 2.0
                    if is_all_white or is_all_black:
                        # Degenerate: try baseColorFactor from the material instead
                        color_name = "white" if is_all_white else "black"
                        logger.warning(f"  to_color() returned all-{color_name}, "
                                       f"attempting baseColorFactor extraction")
                        bcf_color = _extract_material_color(mesh)
                        if bcf_color is not None:
                            n_verts = len(mesh.vertices)
                            full_colors = np.tile(bcf_color.reshape(1, -1), (n_verts, 1))
                            mesh.visual = trimesh.visual.ColorVisuals(
                                mesh=mesh,
                                vertex_colors=full_colors,
                            )
                            logger.info(f"  Replaced degenerate colors with material color "
                                        f"{bcf_color[:3].tolist()}")
                            return mesh
                mesh.visual = color_visual
    except Exception as e:
        logger.warning(f"Failed to bake texture to vertex colors: {e}")

        # Fallback: try to extract baseColorFactor directly from PBR material
        bcf_color = _extract_material_color(mesh)
        if bcf_color is not None:
            n_verts = len(mesh.vertices)
            full_colors = np.tile(bcf_color.reshape(1, -1), (n_verts, 1))
            mesh.visual = trimesh.visual.ColorVisuals(
                mesh=mesh,
                vertex_colors=full_colors,
            )
            logger.info(f"  Applied baseColorFactor {bcf_color[:3].tolist()} as fallback")

    return mesh


def _extract_material_color(mesh: trimesh.Trimesh):
    """
    Try to extract a usable RGBA color from the mesh material's
    baseColorFactor or main_color. Returns a (4,) uint8 array or None.
    """
    try:
        if not (hasattr(mesh, 'visual') and hasattr(mesh.visual, 'material')):
            return None
        mat = mesh.visual.material
        bcf = getattr(mat, 'baseColorFactor', None)
        if bcf is None:
            bcf = getattr(mat, 'main_color', None)
        if bcf is None:
            return None
        bcf_arr = np.array(bcf)
        # baseColorFactor may be float [0-1] or uint8 [0-255]
        if bcf_arr.dtype.kind == 'f' and bcf_arr.max() <= 1.0:
            bcf_arr = (bcf_arr * 255).astype(np.uint8)
        else:
            bcf_arr = bcf_arr.astype(np.uint8)
        if len(bcf_arr) < 3:
            return None
        rgba = np.zeros(4, dtype=np.uint8)
        rgba[:min(len(bcf_arr), 4)] = bcf_arr[:4]
        if len(bcf_arr) < 4:
            rgba[3] = 255
        return rgba
    except Exception as e:
        logger.warning(f"Failed to extract material color: {e}")
        return None


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
        # Handle single-color broadcast from PBR material flat colors
        if colors.ndim == 1 and len(colors) >= 3:
            pv_mesh.point_data["RGB"] = np.tile(colors[:3].reshape(1, 3), (len(vertices), 1))
        elif colors.ndim == 2 and colors.shape[1] >= 3 and colors.shape[0] == len(vertices):
            pv_mesh.point_data["RGB"] = colors[:, :3]
        elif colors.ndim == 2 and colors.shape[1] >= 3 and colors.shape[0] == 1:
            pv_mesh.point_data["RGB"] = np.tile(colors[0, :3].reshape(1, 3), (len(vertices), 1))
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
