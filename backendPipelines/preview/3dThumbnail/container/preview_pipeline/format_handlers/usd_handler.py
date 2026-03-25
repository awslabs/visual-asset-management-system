# Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
USD format handler for .usd, .usda, .usdc, .usdz files.
Loads USD geometry using pxr (OpenUSD Python bindings) and converts to PyVista for rendering.

Uses pxr.Usd.Stage.Open() to load all USD variants (including USDZ archives),
traverses stage prims to extract UsdGeom.Mesh geometry, and merges into a single
PyVista PolyData object for the rendering pipeline.

Texture extraction: traverses UsdShade material bindings to find diffuse texture images,
extracts UV coordinates from primvars, and bakes texture colors to per-vertex RGB data.
For USDZ archives, texture images are extracted from the embedded zip.

Reference: https://github.com/beersandrew/usd-thumbnail-generator
"""

import io
import os
import zipfile
import numpy as np
import pyvista as pv
from PIL import Image
from ..utils.logging import get_logger

logger = get_logger()

SUPPORTED_EXTENSIONS = {".usd", ".usda", ".usdc", ".usdz"}


def can_handle(extension: str) -> bool:
    return extension.lower() in SUPPORTED_EXTENSIONS


def load(file_path: str) -> pv.PolyData:
    """
    Load a USD file and return a PyVista PolyData object for rendering.
    Supports: .usd, .usda, .usdc, .usdz

    Extracts all UsdGeom.Mesh prims from the stage, applies world transforms,
    triangulates n-gon faces, extracts displayColor vertex colors, and normalizes
    the up-axis based on USD stage metadata.
    """
    from pxr import Usd, UsdGeom, Gf

    ext = os.path.splitext(file_path)[1].lower()
    logger.info(f"Loading USD file: {file_path} (format: {ext})")

    stage = Usd.Stage.Open(file_path)
    if stage is None:
        raise ValueError(f"Failed to open USD stage: {file_path}")

    all_vertices = []
    all_faces = []
    all_colors = []
    vertex_offset = 0

    # Create a single XformCache for consistent transform evaluation across all prims
    xform_cache = UsdGeom.XformCache()

    # Traverse all prims in the stage
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue

        mesh = UsdGeom.Mesh(prim)

        # Get geometry attributes
        points_attr = mesh.GetPointsAttr()
        face_vertex_counts_attr = mesh.GetFaceVertexCountsAttr()
        face_vertex_indices_attr = mesh.GetFaceVertexIndicesAttr()

        if not points_attr or not face_vertex_counts_attr or not face_vertex_indices_attr:
            logger.warning(f"Skipping prim {prim.GetPath()}: missing geometry attributes")
            continue

        points = points_attr.Get()
        face_vertex_counts = face_vertex_counts_attr.Get()
        face_vertex_indices = face_vertex_indices_attr.Get()

        if points is None or face_vertex_counts is None or face_vertex_indices is None:
            logger.warning(f"Skipping prim {prim.GetPath()}: empty geometry data")
            continue

        # Convert points to numpy and apply world transform
        points_np = np.array(points, dtype=np.float64)
        if len(points_np) == 0:
            logger.warning(f"Skipping prim {prim.GetPath()}: no vertices")
            continue

        world_transform = xform_cache.GetLocalToWorldTransform(prim)
        matrix = np.array(world_transform, dtype=np.float64)

        # Apply 4x4 transform to points (homogeneous coordinates).
        # USD Gf.Matrix4d uses row-vector convention: p' = p * M
        # so the correct multiplication is points_h @ matrix (NOT matrix.T).
        # Transposing was incorrect and caused multi-prim scenes to have
        # wrong world positions, leading to bad camera framing / bounds.
        ones = np.ones((len(points_np), 1), dtype=np.float64)
        points_h = np.hstack([points_np, ones])
        transformed = points_h @ matrix
        points_np = transformed[:, :3]

        # Triangulate faces (handle quads and n-gons)
        triangles = _triangulate_faces(
            list(face_vertex_counts),
            list(face_vertex_indices),
        )

        if not triangles:
            logger.warning(f"Skipping prim {prim.GetPath()}: no triangles after triangulation")
            continue

        # Offset triangle indices
        triangles_np = np.array(triangles, dtype=np.int64) + vertex_offset

        all_vertices.append(points_np)
        all_faces.append(triangles_np)

        # Extract per-vertex colors: try texture-baked colors first, then displayColor,
        # then fall back to the material's base color (flat color from shader graph)
        fvi_list = list(face_vertex_indices)
        colors = _extract_texture_colors(
            stage, prim, file_path, list(face_vertex_counts), fvi_list, len(points_np)
        )
        if colors is None:
            colors = _extract_display_colors(mesh, len(points_np))
        if colors is None:
            colors = _extract_material_base_color(prim, len(points_np))
        if colors is not None:
            all_colors.append(colors)

        vertex_offset += len(points_np)
        logger.info(f"  Prim {prim.GetPath()}: {len(points_np)} vertices, {len(triangles)} triangles")

    if not all_vertices:
        raise ValueError("No valid mesh geometry found in USD stage")

    # Merge all geometry
    merged_vertices = np.vstack(all_vertices)
    merged_faces = np.vstack(all_faces)

    # Build PyVista PolyData
    n_faces = len(merged_faces)
    pv_faces = np.column_stack([np.full(n_faces, 3), merged_faces]).ravel()
    pv_mesh = pv.PolyData(merged_vertices, pv_faces)

    # Apply vertex colors if available
    if all_colors and sum(len(c) for c in all_colors) == len(merged_vertices):
        merged_colors = np.vstack(all_colors)
        pv_mesh.point_data["RGB"] = merged_colors

    # Normalize up-axis based on USD stage metadata
    pv_mesh = _normalize_usd_up_axis(stage, pv_mesh)

    logger.info(
        f"Loaded USD model: {len(merged_vertices)} vertices, {n_faces} faces "
        f"from {len(all_vertices)} mesh prims"
    )
    return pv_mesh


def _extract_material_base_color(prim, num_points):
    """
    Extract a flat base color from the UsdShade material bound to a prim.

    Follows: Mesh → MaterialBindingAPI → Material → UsdPreviewSurface → diffuseColor value.
    Unlike texture extraction, this reads the constant color value (not a texture connection).

    Returns an (N, 3) uint8 numpy array of a single broadcast color, or None.
    """
    from pxr import UsdShade, Gf

    try:
        binding_api = UsdShade.MaterialBindingAPI(prim)
        result = binding_api.ComputeBoundMaterial()
        material = result[0] if isinstance(result, (tuple, list)) else result

        if not material or not material.GetPrim().IsValid():
            return None

        # Find UsdPreviewSurface shader and read diffuseColor input value
        from pxr import Usd
        for desc in Usd.PrimRange(material.GetPrim()):
            shader = UsdShade.Shader(desc)
            shader_id_attr = shader.GetIdAttr()
            if not shader_id_attr:
                continue
            if shader_id_attr.Get() != "UsdPreviewSurface":
                continue

            diffuse_input = shader.GetInput("diffuseColor")
            if diffuse_input is None:
                continue

            # Check if it's connected to a texture (skip — texture path handles that)
            if diffuse_input.HasConnectedSource():
                continue

            # Read the constant value
            val = diffuse_input.Get()
            if val is not None:
                if isinstance(val, Gf.Vec3f):
                    rgb = np.array([val[0], val[1], val[2]], dtype=np.float64)
                else:
                    rgb = np.array(val, dtype=np.float64)[:3]

                # Convert from float [0,1] to uint8 [0,255]
                rgb_uint8 = (np.clip(rgb, 0.0, 1.0) * 255).astype(np.uint8)
                # Broadcast to all vertices
                colors = np.tile(rgb_uint8.reshape(1, 3), (num_points, 1))
                logger.info(f"    Extracted material base color: {rgb_uint8.tolist()}")
                return colors

    except Exception as e:
        logger.debug(f"    Material base color extraction failed: {e}")

    return None


def _triangulate_faces(face_vertex_counts, face_vertex_indices):
    """
    Triangulate polygon faces into triangles.
    Handles triangles (3), quads (4), and arbitrary n-gons via fan triangulation.
    """
    triangles = []
    idx = 0
    indices_len = len(face_vertex_indices)

    for count in face_vertex_counts:
        if count < 3:
            idx += count
            continue

        # Bounds check to handle malformed USD files
        if idx + count > indices_len:
            logger.warning(
                f"Face vertex indices out of bounds at idx={idx}, count={count}, "
                f"total indices={indices_len}. Stopping triangulation."
            )
            break

        # Fan triangulation from first vertex
        v0 = face_vertex_indices[idx]
        for i in range(1, count - 1):
            v1 = face_vertex_indices[idx + i]
            v2 = face_vertex_indices[idx + i + 1]
            triangles.append([v0, v1, v2])
        idx += count
    return triangles


def _extract_display_colors(mesh, num_points):
    """
    Extract displayColor primvar from a UsdGeom.Mesh.
    Returns an (N, 3) uint8 numpy array of RGB colors, or None if not available.
    """
    from pxr import UsdGeom

    primvar_api = UsdGeom.PrimvarsAPI(mesh.GetPrim())
    color_primvar = primvar_api.GetPrimvar("displayColor")

    if not color_primvar or not color_primvar.HasValue():
        return None

    try:
        colors_raw = color_primvar.Get()
        if colors_raw is None or len(colors_raw) == 0:
            return None

        colors_np = np.array(colors_raw, dtype=np.float64)

        # Handle constant interpolation (single color for entire mesh)
        if len(colors_np.shape) == 1:
            colors_np = colors_np.reshape(1, -1)

        # Validate color channels (need at least 3 for RGB)
        if colors_np.shape[-1] < 3:
            logger.warning(
                f"displayColor has only {colors_np.shape[-1]} channel(s), expected >= 3 (RGB). Skipping colors."
            )
            return None

        if colors_np.shape[0] == 1:
            # Broadcast single color to all vertices
            colors_np = np.tile(colors_np, (num_points, 1))
        elif colors_np.shape[0] != num_points:
            # Face-varying or other interpolation - skip for now
            logger.warning(
                f"displayColor count ({colors_np.shape[0]}) != vertex count ({num_points}), skipping colors"
            )
            return None

        # Convert from float [0,1] to uint8 [0,255]
        colors_uint8 = (np.clip(colors_np[:, :3], 0.0, 1.0) * 255).astype(np.uint8)
        return colors_uint8

    except Exception as e:
        logger.warning(f"Failed to extract displayColor: {e}")
        return None


def _extract_texture_colors(stage, prim, file_path, face_vertex_counts, face_vertex_indices, num_points):
    """
    Try to extract texture-baked per-vertex colors from a USD mesh prim.
    Traverses UsdShade material binding → shader graph → texture file,
    then samples the texture at UV coordinates to produce per-vertex RGB colors.

    Returns (N, 3) uint8 numpy array or None if textures are not available.
    """
    try:
        # Step 1: Get UV coordinates
        uvs, interpolation = _get_uv_coords(prim)
        if uvs is None:
            return None

        # Step 2: Find diffuse texture path from material
        texture_path = _get_diffuse_texture_path(prim)
        if texture_path is None:
            return None

        # Step 3: Load texture image
        texture_image = _load_texture_image(texture_path, file_path)
        if texture_image is None:
            return None

        # Step 4: Bake texture to per-vertex colors
        colors = _bake_texture_to_colors(
            texture_image, uvs, interpolation,
            face_vertex_indices, num_points,
        )

        if colors is not None:
            logger.info(f"    Baked texture colors from {os.path.basename(texture_path)}")

        return colors

    except Exception as e:
        logger.debug(f"    Texture extraction failed: {e}")
        return None


def _get_uv_coords(prim):
    """
    Extract UV texture coordinates from a mesh prim's primvars.

    Checks common USD UV primvar names first, then falls back to scanning
    all primvars for 2D float arrays that look like UV coordinates.

    Returns (uvs_array, interpolation_string) or (None, None).
    """
    from pxr import UsdGeom, Sdf

    primvar_api = UsdGeom.PrimvarsAPI(prim)

    # Common UV primvar names (ordered by frequency)
    common_names = ["st", "UVMap", "st0", "texCoord_0", "Texture_uv", "uv"]
    for name in common_names:
        pvar = primvar_api.GetPrimvar(name)
        if pvar and pvar.HasValue():
            uvs_raw = pvar.Get()
            if uvs_raw is not None and len(uvs_raw) > 0:
                uvs = np.array(uvs_raw, dtype=np.float64)
                if uvs.ndim == 2 and uvs.shape[1] >= 2:
                    interpolation = str(pvar.GetInterpolation())
                    return uvs[:, :2], interpolation

    # Fallback: scan all primvars for float2/texCoord2f arrays
    for pvar in primvar_api.GetPrimvars():
        if not pvar.HasValue():
            continue
        type_name = str(pvar.GetTypeName())
        if "2f" in type_name or "float2" in type_name or "texCoord" in type_name:
            uvs_raw = pvar.Get()
            if uvs_raw is not None and len(uvs_raw) > 0:
                uvs = np.array(uvs_raw, dtype=np.float64)
                if uvs.ndim == 2 and uvs.shape[1] >= 2:
                    interpolation = str(pvar.GetInterpolation())
                    logger.info(f"    Found UV primvar: {pvar.GetName()} ({type_name})")
                    return uvs[:, :2], interpolation

    return None, None


def _get_diffuse_texture_path(prim):
    """
    Traverse USD material binding → shader graph to find the diffuse texture file path.

    Path: Mesh → MaterialBindingAPI → Material → UsdPreviewSurface
          → diffuseColor input → UsdUVTexture → inputs:file
    """
    from pxr import UsdShade

    try:
        # Get bound material
        binding_api = UsdShade.MaterialBindingAPI(prim)
        result = binding_api.ComputeBoundMaterial()
        material = result[0] if isinstance(result, (tuple, list)) else result

        if not material or not material.GetPrim().IsValid():
            return None

        material_prim = material.GetPrim()

        # Strategy 1: Follow UsdPreviewSurface → diffuseColor → UsdUVTexture
        texture_path = _traverse_material_for_diffuse(material_prim)
        if texture_path:
            return texture_path

        # Strategy 2: Find any UsdUVTexture shader in the material (fallback)
        texture_path = _find_any_texture_in_material(material_prim)
        return texture_path

    except Exception as e:
        logger.debug(f"    Material traversal failed: {e}")
        return None


def _traverse_material_for_diffuse(material_prim):
    """
    Find the diffuse texture by locating UsdPreviewSurface shaders and following
    their diffuseColor input connection to a UsdUVTexture shader.
    """
    from pxr import UsdShade, Usd

    for desc in Usd.PrimRange(material_prim):
        shader = UsdShade.Shader(desc)
        shader_id_attr = shader.GetIdAttr()
        if not shader_id_attr:
            continue

        shader_id = shader_id_attr.Get()
        if shader_id != "UsdPreviewSurface":
            continue

        # Found UsdPreviewSurface — check diffuseColor input
        diffuse_input = shader.GetInput("diffuseColor")
        if diffuse_input is None or not diffuse_input.HasConnectedSource():
            continue

        # Follow connection to texture shader
        try:
            source = diffuse_input.GetConnectedSource()
            if not source or not source[0]:
                continue
            source_prim = source[0].GetPrim()
        except Exception:
            continue

        tex_shader = UsdShade.Shader(source_prim)
        tex_id_attr = tex_shader.GetIdAttr()
        if tex_id_attr and tex_id_attr.Get() == "UsdUVTexture":
            file_input = tex_shader.GetInput("file")
            if file_input:
                path = _get_asset_path(file_input.Get())
                if path:
                    return path

    return None


def _find_any_texture_in_material(material_prim):
    """
    Fallback: find the first UsdUVTexture shader with a file input
    among the material's descendants.
    """
    from pxr import UsdShade, Usd

    for desc in Usd.PrimRange(material_prim):
        shader = UsdShade.Shader(desc)
        shader_id_attr = shader.GetIdAttr()
        if not shader_id_attr:
            continue

        if shader_id_attr.Get() == "UsdUVTexture":
            file_input = shader.GetInput("file")
            if file_input:
                path = _get_asset_path(file_input.Get())
                if path:
                    return path

    return None


def _get_asset_path(sdf_asset_path):
    """
    Extract the file path string from an SdfAssetPath value.

    Prefers the authored .path over .resolvedPath because the resolved path
    for USDZ-embedded assets uses package notation (e.g. 'file.usdz[tex.png]')
    which our texture loader handles via zipfile extraction instead.
    """
    if sdf_asset_path is None:
        return None

    # Prefer authored path (relative name like "textures/diffuse.png")
    if hasattr(sdf_asset_path, "path") and sdf_asset_path.path:
        return sdf_asset_path.path

    # Fall back to resolved path (may include package notation for USDZ)
    if hasattr(sdf_asset_path, "resolvedPath") and sdf_asset_path.resolvedPath:
        return sdf_asset_path.resolvedPath

    path_str = str(sdf_asset_path)
    if path_str and path_str != "@@":
        return path_str.strip("@")

    return None


def _load_texture_image(texture_path, usd_file_path):
    """
    Load a texture image from the filesystem or from inside a USDZ archive.
    Returns a PIL Image in RGB mode, or None on failure.
    """
    ext = os.path.splitext(usd_file_path)[1].lower()

    # For USDZ files, textures are embedded in the zip archive
    if ext == ".usdz":
        return _load_texture_from_usdz(usd_file_path, texture_path)

    # For regular USD files, resolve the texture path using multiple strategies
    # 1. Absolute path
    if os.path.isabs(texture_path) and os.path.isfile(texture_path):
        try:
            return Image.open(texture_path).convert("RGB")
        except Exception:
            pass

    usd_dir = os.path.dirname(os.path.abspath(usd_file_path))
    basename = os.path.basename(texture_path)

    # 2. Relative to USD file directory
    abs_path = os.path.join(usd_dir, texture_path)
    if os.path.isfile(abs_path):
        try:
            return Image.open(abs_path).convert("RGB")
        except Exception:
            pass

    # 3. Search parent directories (up to 3 levels) for the texture filename
    # USD scenes sometimes reference textures in sibling or parent folders
    search_dir = usd_dir
    for _ in range(3):
        parent = os.path.dirname(search_dir)
        if parent == search_dir:
            break  # Hit filesystem root
        search_dir = parent
        candidate = os.path.join(search_dir, texture_path)
        if os.path.isfile(candidate):
            try:
                return Image.open(candidate).convert("RGB")
            except Exception:
                pass

    # 4. Recursive basename search in the USD directory tree
    # Handles cases where textures are in a subdirectory like textures/, materials/, etc.
    for root, dirs, files in os.walk(usd_dir):
        if basename in files:
            candidate = os.path.join(root, basename)
            try:
                return Image.open(candidate).convert("RGB")
            except Exception:
                pass
        # Also search one level up from the USD directory
        # (common when USD is in a subfolder alongside a textures folder)

    # 5. Search one directory up from USD file
    parent_dir = os.path.dirname(usd_dir)
    if parent_dir != usd_dir:
        for root, dirs, files in os.walk(parent_dir):
            if basename in files:
                candidate = os.path.join(root, basename)
                try:
                    return Image.open(candidate).convert("RGB")
                except Exception:
                    pass

    logger.warning(f"    Could not load texture: {texture_path} (searched {usd_dir} and parent directories)")
    return None


def _load_texture_from_usdz(usdz_path, texture_rel_path):
    """
    Load a texture image from inside a USDZ archive.
    USDZ is an uncompressed zip containing a .usdc scene + texture files.
    """
    try:
        with zipfile.ZipFile(usdz_path, "r") as zf:
            names = zf.namelist()
            clean_path = texture_rel_path.lstrip("./")

            # Strategy 1: Exact path match
            if clean_path in names:
                with zf.open(clean_path) as f:
                    return Image.open(io.BytesIO(f.read())).convert("RGB")

            # Strategy 2: Basename match (handles path prefix differences)
            basename = os.path.basename(clean_path)
            for name in names:
                if os.path.basename(name) == basename:
                    with zf.open(name) as f:
                        return Image.open(io.BytesIO(f.read())).convert("RGB")

    except Exception as e:
        logger.warning(f"    Failed to extract texture from USDZ: {e}")

    return None


def _bake_texture_to_colors(texture_image, uvs, interpolation, face_vertex_indices, num_points):
    """
    Sample a texture image at UV coordinates to produce per-vertex RGB colors.

    Handles both 'vertex' and 'faceVarying' UV interpolation.
    For faceVarying, accumulates texture samples per vertex and averages them
    (handles shared vertices at UV seams gracefully).
    """
    tex_array = np.array(texture_image)
    if tex_array.ndim < 3 or tex_array.shape[2] < 3:
        return None

    h, w = tex_array.shape[:2]

    if interpolation == "faceVarying":
        # UVs are per face-vertex (one per entry in face_vertex_indices)
        fvi = np.array(face_vertex_indices)
        num_fv = min(len(uvs), len(fvi))

        uv_coords = uvs[:num_fv]
        px = np.clip((uv_coords[:, 0] % 1.0) * (w - 1), 0, w - 1).astype(np.int32)
        py = np.clip((1.0 - uv_coords[:, 1] % 1.0) * (h - 1), 0, h - 1).astype(np.int32)
        sampled = tex_array[py, px, :3].astype(np.float64)

        # Accumulate per vertex and average
        color_accum = np.zeros((num_points, 3), dtype=np.float64)
        color_count = np.zeros(num_points, dtype=np.int32)
        np.add.at(color_accum, fvi[:num_fv], sampled)
        np.add.at(color_count, fvi[:num_fv], 1)

        mask = color_count > 0
        color_accum[mask] /= color_count[mask, np.newaxis]
        return color_accum.astype(np.uint8)

    else:
        # Per-vertex UVs (vertex or uniform interpolation)
        n = min(len(uvs), num_points)
        uv_coords = uvs[:n]
        px = np.clip((uv_coords[:, 0] % 1.0) * (w - 1), 0, w - 1).astype(np.int32)
        py = np.clip((1.0 - uv_coords[:, 1] % 1.0) * (h - 1), 0, h - 1).astype(np.int32)

        colors = np.zeros((num_points, 3), dtype=np.uint8)
        colors[:n] = tex_array[py, px, :3]
        return colors


def _normalize_usd_up_axis(stage, pv_data):
    """
    Detect up-axis from USD stage metadata and rotate Z-up to Y-up if needed.
    USD stages declare their up-axis via UsdGeom.GetStageUpAxis().
    """
    from pxr import UsdGeom

    up_axis = UsdGeom.GetStageUpAxis(stage)
    logger.info(f"USD stage up-axis: {up_axis}")

    if up_axis == UsdGeom.Tokens.z:
        logger.info("USD stage is Z-up, rotating to Y-up for rendering")
        points = pv_data.points.copy()
        new_points = np.empty_like(points)
        new_points[:, 0] = points[:, 0]    # X stays
        new_points[:, 1] = points[:, 2]    # Y = old Z (up axis)
        new_points[:, 2] = -points[:, 1]   # Z = -old Y
        pv_data.points = new_points
    else:
        logger.info(f"USD stage is {up_axis}-up (no rotation needed)")

    return pv_data
