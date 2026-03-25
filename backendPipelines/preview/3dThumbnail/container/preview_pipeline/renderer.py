# Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Rendering logic for generating preview images from PyVista data.
Uses PyVista with Xvfb for CPU-based headless offscreen rendering.
Xvfb is started by the container entrypoint (see Dockerfile).

Camera framing uses percentile-based bounds (2nd-98th) instead of the full
bounding box so that sparse scenes (e.g. single-position scans with distant
outliers) are framed tightly around the dense content.
"""

import numpy as np
import pyvista as pv
from .utils.logging import get_logger

logger = get_logger()

# Default rendering parameters
DEFAULT_N_FRAMES = 36
DEFAULT_RESOLUTION = (800, 600)
POINT_SIZE = 2.0

# Background color for rendering. Mid-gray minimizes visible halos on
# anti-aliased edges when composited over both light and dark backgrounds,
# since the blended edge pixels are close to neutral.
_BG_COLOR = (128, 128, 128)

# Percentile range for camera focus region (crops outliers)
_FOCUS_PERCENTILE_LO = 2
_FOCUS_PERCENTILE_HI = 98

# Camera elevation angle in degrees above the orbit plane.
# 25 degrees is the industry standard for 3D product thumbnails.
_CAMERA_ELEVATION_DEG = 25


def generate_rotating_frames(
    pv_data: pv.PolyData,
    n_frames: int = DEFAULT_N_FRAMES,
    resolution: tuple = DEFAULT_RESOLUTION,
    use_full_bounds: bool = False,
) -> list:
    """
    Render a rotating sequence of frames around the Y-axis of the given PyVista data.
    Returns RGBA frames with transparent background.
    """
    logger.info(f"Generating {n_frames} rotating frames at {resolution}")

    plotter = pv.Plotter(off_screen=True, window_size=resolution)
    plotter.set_background(_BG_COLOR)

    is_point_cloud = pv_data.n_cells == 0 or pv_data.n_cells == pv_data.n_points

    if is_point_cloud:
        _add_point_cloud(plotter, pv_data)
    else:
        _add_mesh(plotter, pv_data)

    focal_point, radius, elevation = _compute_camera_framing(pv_data, use_full_bounds=use_full_bounds)

    frames = []
    for i in range(n_frames):
        angle_rad = np.radians(i * (360.0 / n_frames))

        cam_x = focal_point[0] + radius * np.cos(angle_rad)
        cam_z = focal_point[2] + radius * np.sin(angle_rad)
        cam_y = focal_point[1] + elevation

        plotter.camera.position = (cam_x, cam_y, cam_z)
        plotter.camera.focal_point = tuple(focal_point)
        plotter.camera.up = (0.0, 1.0, 0.0)

        plotter.render()
        img = plotter.screenshot(return_img=True)
        img = _add_alpha_from_depth(plotter, img)
        frames.append(img)

    plotter.close()
    logger.info(f"Generated {len(frames)} frames")
    return frames


def generate_static_frame(
    pv_data: pv.PolyData,
    resolution: tuple = DEFAULT_RESOLUTION,
    use_full_bounds: bool = False,
) -> np.ndarray:
    """
    Render a single static frame of the given PyVista data.
    Returns RGBA image with transparent background.
    """
    logger.info(f"Generating static frame at {resolution}")

    plotter = pv.Plotter(off_screen=True, window_size=resolution)
    plotter.set_background(_BG_COLOR)

    is_point_cloud = pv_data.n_cells == 0 or pv_data.n_cells == pv_data.n_points

    if is_point_cloud:
        _add_point_cloud(plotter, pv_data)
    else:
        _add_mesh(plotter, pv_data)

    focal_point, radius, elevation = _compute_camera_framing(pv_data, use_full_bounds=use_full_bounds)

    angle_rad = np.radians(45)
    cam_x = focal_point[0] + radius * np.cos(angle_rad)
    cam_z = focal_point[2] + radius * np.sin(angle_rad)
    cam_y = focal_point[1] + elevation

    plotter.camera.position = (cam_x, cam_y, cam_z)
    plotter.camera.focal_point = tuple(focal_point)
    plotter.camera.up = (0.0, 1.0, 0.0)

    plotter.render()
    img = plotter.screenshot(return_img=True)
    img = _add_alpha_from_depth(plotter, img)
    plotter.close()

    return img


def _add_mesh(plotter: pv.Plotter, pv_data: pv.PolyData):
    """Add a mesh to the plotter with appropriate styling."""
    texture = getattr(pv_data, '_preview_texture', None)
    has_colors = "RGB" in pv_data.point_data

    if texture is not None:
        logger.info("Rendering with UV texture mapping")
        plotter.add_mesh(
            pv_data,
            texture=texture,
            smooth_shading=True,
            show_edges=False,
            specular=0.5,
            specular_power=15,
        )
    elif has_colors:
        plotter.add_mesh(
            pv_data,
            scalars="RGB",
            rgb=True,
            smooth_shading=True,
            show_edges=False,
        )
    else:
        plotter.add_mesh(
            pv_data,
            color="lightblue",
            smooth_shading=True,
            show_edges=False,
            specular=0.5,
            specular_power=15,
        )

    plotter.add_light(pv.Light(position=(1, 1, 1), intensity=0.8))


def _add_point_cloud(plotter: pv.Plotter, pv_data: pv.PolyData):
    """Add a point cloud to the plotter with appropriate styling."""
    has_colors = "RGB" in pv_data.point_data

    if has_colors:
        plotter.add_mesh(
            pv_data,
            scalars="RGB",
            rgb=True,
            point_size=POINT_SIZE,
            render_points_as_spheres=True,
        )
    else:
        plotter.add_mesh(
            pv_data,
            scalars=pv_data.points[:, 1],
            cmap="viridis",
            point_size=POINT_SIZE,
            render_points_as_spheres=True,
            show_scalar_bar=False,
        )


def _add_alpha_from_depth(plotter, img_rgb):
    """
    Create RGBA image using the Z-buffer (depth buffer) to determine transparency.

    The depth buffer contains the distance from the camera to each pixel.
    Background pixels have depth = 1.0 (far plane). Any pixel with geometry
    has depth < 1.0. This gives a perfect binary mask regardless of model
    color, texture, or sparsity — no color matching needed.

    This works for all model types: solid meshes, sparse point clouds,
    textured models, white surfaces, and models with holes.
    """
    # Get the depth buffer from VTK's renderer
    z_buffer = plotter.get_image_depth()

    h, w = img_rgb.shape[:2]
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[:, :, :3] = img_rgb[:, :, :3]

    # Background pixels have depth == 1.0 (far clipping plane)
    # Model pixels have depth < 1.0
    has_geometry = z_buffer < 0.9999

    # Erode the geometry mask by 1 pixel to remove anti-aliased edge fringe
    # that picks up background color blending
    from scipy.ndimage import binary_erosion
    has_geometry = binary_erosion(has_geometry, iterations=1)

    rgba[:, :, 3] = np.where(has_geometry, 255, 0).astype(np.uint8)

    return rgba


def _compute_camera_framing(pv_data, use_full_bounds=False):
    """
    Compute camera framing parameters for orbit-style camera positioning.

    For percentile mode (default): uses 2nd-98th percentile bounds and
    diagonal * 1.8 radius — the proven formula for meshes and point clouds.

    For full-bounds mode (USD/CAD): uses complete bounding box and
    diagonal * 1.4 radius — tighter because full bounds already includes
    all geometry without percentile cropping.
    """
    points = pv_data.points

    if use_full_bounds:
        lo = points.min(axis=0)
        hi = points.max(axis=0)
    else:
        lo = np.percentile(points, _FOCUS_PERCENTILE_LO, axis=0)
        hi = np.percentile(points, _FOCUS_PERCENTILE_HI, axis=0)

    focal_point = (lo + hi) / 2.0

    dx = hi[0] - lo[0]
    dy = hi[1] - lo[1]
    dz = hi[2] - lo[2]

    diagonal = np.sqrt(dx**2 + dy**2 + dz**2)

    if use_full_bounds:
        # For engineered models (USD/CAD): use per-axis FOV calculation.
        # This frames the model tightly based on actual visible extents
        # rather than the full 3D diagonal which overestimates.
        half_fov_rad = np.radians(15.0)  # Half of ~30 degree default FOV
        tan_half_fov = np.tan(half_fov_rad)
        dist_for_height = (dy / 2.0) / tan_half_fov if dy > 0 else 0
        horizontal_extent = max(dx, dz)
        dist_for_width = (horizontal_extent / 2.0) / tan_half_fov if horizontal_extent > 0 else 0
        radius = max(dist_for_height, dist_for_width, 1e-6) * 1.05
    else:
        # Proven formula for point clouds, meshes, and general 3D data
        radius = max(diagonal * 1.8, 1e-6)

    elevation = radius * np.tan(np.radians(_CAMERA_ELEVATION_DEG))

    logger.info(
        f"Camera framing: focal={focal_point}, radius={radius:.2f}, "
        f"elevation={elevation:.2f}, focus_extents=({dx:.2f}, {dy:.2f}, {dz:.2f}), "
        f"use_full_bounds={use_full_bounds}"
    )

    return focal_point, radius, elevation
