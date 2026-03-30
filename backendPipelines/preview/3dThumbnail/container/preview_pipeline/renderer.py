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

    focal_point, radius, elevation = _compute_camera_framing(
        pv_data, use_full_bounds=use_full_bounds, resolution=resolution
    )

    # Compute stable clipping range from the full model extent so VTK
    # does not auto-adjust per frame (which causes near-plane clipping
    # and depth-buffer flicker for wide models at certain orbit angles).
    full_diagonal = np.linalg.norm(pv_data.points.max(axis=0) - pv_data.points.min(axis=0))
    cam_distance = np.sqrt(radius**2 + elevation**2)
    near_clip = max((cam_distance - full_diagonal) * 0.5, cam_distance * 0.001)
    far_clip = cam_distance + full_diagonal * 2.0

    frames = []
    for i in range(n_frames):
        angle_rad = np.radians(i * (360.0 / n_frames))

        cam_x = focal_point[0] + radius * np.cos(angle_rad)
        cam_z = focal_point[2] + radius * np.sin(angle_rad)
        cam_y = focal_point[1] + elevation

        plotter.camera.position = (cam_x, cam_y, cam_z)
        plotter.camera.focal_point = tuple(focal_point)
        plotter.camera.up = (0.0, 1.0, 0.0)
        plotter.camera.clipping_range = (near_clip, far_clip)

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

    focal_point, radius, elevation = _compute_camera_framing(
        pv_data, use_full_bounds=use_full_bounds, resolution=resolution
    )

    full_diagonal = np.linalg.norm(pv_data.points.max(axis=0) - pv_data.points.min(axis=0))
    cam_distance = np.sqrt(radius**2 + elevation**2)
    near_clip = max((cam_distance - full_diagonal) * 0.5, cam_distance * 0.001)
    far_clip = cam_distance + full_diagonal * 2.0

    angle_rad = np.radians(45)
    cam_x = focal_point[0] + radius * np.cos(angle_rad)
    cam_z = focal_point[2] + radius * np.sin(angle_rad)
    cam_y = focal_point[1] + elevation

    plotter.camera.position = (cam_x, cam_y, cam_z)
    plotter.camera.focal_point = tuple(focal_point)
    plotter.camera.up = (0.0, 1.0, 0.0)
    plotter.camera.clipping_range = (near_clip, far_clip)

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


def _compute_camera_framing(pv_data, use_full_bounds=False, resolution=DEFAULT_RESOLUTION):
    """
    Compute camera framing parameters for orbit-style camera positioning.

    Uses per-axis FOV calculation that accounts for viewport aspect ratio
    and the maximum visible horizontal extent during a full orbit (the XZ
    diagonal, since the model is viewed from all angles).

    For percentile mode (default): uses 2nd-98th percentile bounds to
    crop outliers from sparse scenes (point clouds with distant noise).

    For full-bounds mode (USD/CAD): uses complete bounding box since
    all geometry in engineered models is intentional.
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

    # FOV-based framing: compute required distance for each axis.
    # Default VTK camera has ~30 degree vertical FOV.
    half_fov_rad = np.radians(15.0)
    tan_half_fov = np.tan(half_fov_rad)

    # Horizontal FOV is wider due to viewport aspect ratio
    aspect_ratio = resolution[0] / resolution[1]  # e.g. 800/600 = 1.333
    tan_half_fov_h = tan_half_fov * aspect_ratio

    # During a full orbit, the camera sees the model from all angles.
    # The worst-case horizontal extent is the XZ diagonal (corner-on view).
    xz_diagonal = np.sqrt(dx**2 + dz**2)

    # Distance needed to fit vertical extent (Y axis)
    dist_for_height = (dy / 2.0) / tan_half_fov if dy > 0 else 0

    # Distance needed to fit horizontal extent (XZ diagonal during orbit)
    dist_for_width = (xz_diagonal / 2.0) / tan_half_fov_h if xz_diagonal > 0 else 0

    # Use the larger of the two with padding
    padding = 1.15 if use_full_bounds else 1.25
    radius = max(dist_for_height, dist_for_width, 1e-6) * padding

    elevation = radius * np.tan(np.radians(_CAMERA_ELEVATION_DEG))

    logger.info(
        f"Camera framing: focal={focal_point}, radius={radius:.2f}, "
        f"elevation={elevation:.2f}, focus_extents=({dx:.2f}, {dy:.2f}, {dz:.2f}), "
        f"xz_diagonal={xz_diagonal:.2f}, dist_h={dist_for_height:.2f}, "
        f"dist_w={dist_for_width:.2f}, use_full_bounds={use_full_bounds}"
    )

    return focal_point, radius, elevation
