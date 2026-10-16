# Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Mesh format handler for .ply, .stl, .obj, .glb, .fbx, .drc files.
Loads mesh geometry using trimesh and converts to PyVista for rendering.
"""

import os
import re
import numpy as np
import trimesh
import pyvista as pv
from ..utils.logging import get_logger

logger = get_logger()

SUPPORTED_EXTENSIONS = {".ply", ".stl", ".obj", ".glb", ".gltf", ".fbx", ".drc", ".off"}


def can_handle(extension: str) -> bool:
    return extension.lower() in SUPPORTED_EXTENSIONS


# FBX GlobalSettings: UpAxis 0 = X, 1 = Y, 2 = Z. ASCII: P: "UpAxis", "int", "Integer", "",2 ;
# binary: the property record's strings followed by an 'I' int32.
_FBX_UP_AXIS_ASCII = re.compile(rb'"UpAxis"\s*,\s*"int"\s*,\s*"Integer"\s*,\s*"[^"]*"\s*,\s*(-?\d+)')
_FBX_UP_AXIS_BINARY = re.compile(rb"UpAxisS\x03\x00\x00\x00intS\x07\x00\x00\x00IntegerS\x00\x00\x00\x00I(.{4})",
                                 re.DOTALL)
_FBX_HEADER_SCAN = 64 * 1024 ** 2


def fbx_up_axis(file_path: str):
    """'X', 'Y' or 'Z' from an FBX file's GlobalSettings ``UpAxis``, or None when it is not found."""
    try:
        with open(file_path, "rb") as handle:
            data = handle.read(_FBX_HEADER_SCAN)
    except OSError:
        return None
    match = _FBX_UP_AXIS_ASCII.search(data)
    value = int(match.group(1)) if match else None
    if value is None:
        match = _FBX_UP_AXIS_BINARY.search(data)
        value = int.from_bytes(match.group(1), "little", signed=True) if match else None
    return {0: "X", 1: "Y", 2: "Z"}.get(value)


def load(file_path: str) -> pv.PolyData:
    """Load a mesh file; an FBX carries the up axis its GlobalSettings declare."""
    data = _load(file_path)
    if os.path.splitext(file_path)[1].lower() == ".fbx":
        axis = fbx_up_axis(file_path)
        if axis:
            logger.info(f"FBX UpAxis: {axis}")
            data._preview_up_axis = axis
    return data


def _load(file_path: str) -> pv.PolyData:
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
        clouds = []
        for geom in scene_or_mesh.dump():
            if isinstance(geom, trimesh.Trimesh):
                trimeshes.append(geom)
            elif isinstance(geom, trimesh.PointCloud):
                clouds.append(geom)
        if clouds:
            # A scene holding point clouds renders every one of its parts, meshes included.
            return _scene_parts_to_pyvista(trimeshes, clouds)
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
                # Create PyVista texture from the base color image, as RGB
                pv_mesh._preview_texture = pv.Texture(texture_rgb_array(tex_image))
                logger.info(f"Loaded textured mesh: {len(vertices)} vertices, {n_faces} faces, "
                            f"texture={tex_image.size}, mode={getattr(tex_image, 'mode', None)}")
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


def texture_rgb_array(image) -> np.ndarray:
    """A texture image as a contiguous HxWx3 uint8 RGB array.

    PIL keeps an image in its stored mode, and ``np.array`` of it keeps that layout: a palette PNG
    (mode ``P``) becomes a 2-D array of palette indices, a greyscale one a single channel and a 16-bit
    one values up to 65535. VTK reads a 2-D array as intensity, so a palette texture rendered as its
    small index values, nearly black (#476: the Khronos Duck's ``DuckCM.png`` holds indices 0..53).
    Every mode is converted to RGB here; alpha is dropped, as the USD loader does.
    """
    from PIL import Image

    if not isinstance(image, Image.Image):
        array = np.asarray(image)
        if array.dtype != np.uint8:
            peak = 65535.0 if array.max(initial=0) > 255 else 255.0
            array = np.clip(array.astype(np.float64) / peak * 255.0 + 0.5, 0, 255).astype(np.uint8)
        image = Image.fromarray(array)
    if image.mode.startswith("I"):  # 16- or 32-bit integer greyscale
        values = np.asarray(image, dtype=np.float64)
        peak = 65535.0 if values.max(initial=0) > 255 else 255.0
        image = Image.fromarray(np.clip(values / peak * 255.0 + 0.5, 0, 255).astype(np.uint8), "L")
    return np.ascontiguousarray(np.asarray(image.convert("RGB"), dtype=np.uint8))


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


# Colours for the parts of a mixed scene that carry none: the renderer's uncoloured-mesh colour, and a
# neutral grey for points.
_MIXED_SCENE_MESH_RGB = (173, 216, 230)
_MIXED_SCENE_POINT_RGB = (200, 200, 200)


def _with_rgb(pv_data: pv.PolyData, default_rgb) -> pv.PolyData:
    """The data with a uint8 per-point RGB array, filled with default_rgb when it has none."""
    rgb = pv_data.point_data.get("RGB") if "RGB" in pv_data.point_data else None
    if rgb is None or len(rgb) != pv_data.n_points:
        rgb = np.tile(np.asarray(default_rgb, dtype=np.uint8), (pv_data.n_points, 1))
    pv_data.point_data["RGB"] = np.asarray(rgb)[:, :3].astype(np.uint8)
    return pv_data


def _scene_parts_to_pyvista(trimeshes: list, clouds: list) -> pv.PolyData:
    """One PolyData holding every mesh and point cloud of a scene.

    Each mesh's texture is baked to vertex colours (one PolyData carries one texture), and every part
    gets a per-point RGB array so the colours survive the append; a point cloud keeps its vertex
    cells, so the renderer draws its points beside the meshes' faces.
    """
    parts = []
    for mesh in trimeshes:
        baked = _bake_texture_to_vertex_colors(mesh)
        if getattr(getattr(baked, "visual", None), "kind", None) == "texture":
            try:
                baked.visual = baked.visual.to_color()
            except Exception:
                pass
        parts.append(_with_rgb(_trimesh_to_pyvista(baked), _MIXED_SCENE_MESH_RGB))
    for cloud in clouds:
        parts.append(_with_rgb(_pointcloud_to_pyvista(cloud), _MIXED_SCENE_POINT_RGB))
    merged = parts[0] if len(parts) == 1 else parts[0].append_polydata(*parts[1:])
    logger.info(f"Loaded scene with {len(trimeshes)} meshes and {len(clouds)} point clouds: "
                f"{merged.n_points} points, {merged.n_cells} cells")
    return merged


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

    colours = getattr(mesh, "colors", None)
    if colours is not None:
        colours = np.asarray(colours)
        if colours.ndim == 2 and colours.shape[0] == len(vertices) and colours.shape[1] >= 3:
            if colours.dtype.kind == "f":
                scale = 255.0 if colours.max(initial=0) <= 1.0 else 1.0
                colours = np.clip(colours[:, :3] * scale + 0.5, 0, 255)
            pv_mesh.point_data["RGB"] = colours[:, :3].astype(np.uint8)
            logger.info("Draco mesh carries vertex colours")

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
