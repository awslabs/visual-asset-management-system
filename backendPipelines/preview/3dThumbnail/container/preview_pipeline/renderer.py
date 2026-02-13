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
DEFAULT_BG_COLOR = "white"
POINT_SIZE = 2.0

# Percentile range for camera focus region (crops outliers)
_FOCUS_PERCENTILE_LO = 2
_FOCUS_PERCENTILE_HI = 98

# Camera elevation angle in degrees above the orbit plane.
# 25° is the industry standard for 3D product thumbnails — provides a "hero shot"
# 3/4 view that reveals top surfaces without being too steep.
_CAMERA_ELEVATION_DEG = 25


def generate_rotating_frames(
    pv_data: pv.PolyData,
    n_frames: int = DEFAULT_N_FRAMES,
    resolution: tuple = DEFAULT_RESOLUTION,
) -> list:
    """
    Render a rotating sequence of frames around the Y-axis of the given PyVista data.

    Args:
        pv_data: PyVista PolyData (mesh or point cloud)
        n_frames: number of frames for the rotation (default 36 = 10 degrees per step)
        resolution: tuple of (width, height) for rendering

    Returns:
        List of numpy arrays (H, W, 3) uint8, one per frame
    """
    logger.info(f"Generating {n_frames} rotating frames at {resolution}")

    plotter = pv.Plotter(off_screen=True, window_size=resolution)
    plotter.set_background(DEFAULT_BG_COLOR)

    # Determine render mode based on data type
    is_point_cloud = pv_data.n_cells == 0 or pv_data.n_cells == pv_data.n_points

    if is_point_cloud:
        _add_point_cloud(plotter, pv_data)
    else:
        _add_mesh(plotter, pv_data)

    # Compute camera framing from percentile-based focus region
    focal_point, radius, elevation = _compute_camera_framing(pv_data)

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
        frames.append(img)

    plotter.close()
    logger.info(f"Generated {len(frames)} frames")
    return frames


def generate_static_frame(
    pv_data: pv.PolyData,
    resolution: tuple = DEFAULT_RESOLUTION,
) -> np.ndarray:
    """
    Render a single static frame of the given PyVista data.

    Args:
        pv_data: PyVista PolyData (mesh or point cloud)
        resolution: tuple of (width, height) for rendering

    Returns:
        numpy array (H, W, 3) uint8
    """
    logger.info(f"Generating static frame at {resolution}")

    plotter = pv.Plotter(off_screen=True, window_size=resolution)
    plotter.set_background(DEFAULT_BG_COLOR)

    is_point_cloud = pv_data.n_cells == 0 or pv_data.n_cells == pv_data.n_points

    if is_point_cloud:
        _add_point_cloud(plotter, pv_data)
    else:
        _add_mesh(plotter, pv_data)

    # Use percentile-based framing for consistent results with sparse scenes
    focal_point, radius, elevation = _compute_camera_framing(pv_data)

    # Isometric-like view at 45 degree azimuth, 20 degree elevation
    angle_rad = np.radians(45)
    cam_x = focal_point[0] + radius * np.cos(angle_rad)
    cam_z = focal_point[2] + radius * np.sin(angle_rad)
    cam_y = focal_point[1] + elevation

    plotter.camera.position = (cam_x, cam_y, cam_z)
    plotter.camera.focal_point = tuple(focal_point)
    plotter.camera.up = (0.0, 1.0, 0.0)

    plotter.render()
    img = plotter.screenshot(return_img=True)
    plotter.close()

    return img


def _add_mesh(plotter: pv.Plotter, pv_data: pv.PolyData):
    """Add a mesh to the plotter with appropriate styling."""
    texture = getattr(pv_data, '_preview_texture', None)
    has_colors = "RGB" in pv_data.point_data

    if texture is not None:
        # UV-mapped texture rendering (GLB/GLTF models)
        # Note: PBR is not used because it renders incorrectly in headless/Xvfb mode
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

    # Add subtle lighting
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
        # Color by elevation (Y-axis, which is up after normalization)
        plotter.add_mesh(
            pv_data,
            scalars=pv_data.points[:, 1],
            cmap="viridis",
            point_size=POINT_SIZE,
            render_points_as_spheres=True,
            show_scalar_bar=False,
        )


def _compute_camera_framing(pv_data):
    """
    Compute camera framing parameters based on the percentile-based focus region.

    Uses the 2nd–98th percentile bounds instead of the full bounding box so that
    sparse scenes (e.g. single-position scans with distant outliers) are framed
    tightly around the dense content.

    Returns:
        (focal_point, radius, elevation) where:
          - focal_point: np.array [x, y, z] center of the focus region
          - radius: orbit distance from focal_point in the XZ plane
          - elevation: camera Y offset above focal_point
    """
    points = pv_data.points

    # Compute percentile-based bounds on each axis
    lo = np.percentile(points, _FOCUS_PERCENTILE_LO, axis=0)
    hi = np.percentile(points, _FOCUS_PERCENTILE_HI, axis=0)

    # Focus region center
    focal_point = (lo + hi) / 2.0

    # Focus region extents
    dx = hi[0] - lo[0]
    dy = hi[1] - lo[1]
    dz = hi[2] - lo[2]

    # Diagonal of the focus region bounding box
    diagonal = np.sqrt(dx**2 + dy**2 + dz**2)

    # Orbit radius: enough distance to see the focus region comfortably
    # Factor of 1.8 gives a good field-of-view coverage
    radius = max(diagonal * 1.8, 1e-6)

    # Elevation: fixed angle above the orbit plane for a consistent "hero shot"
    # 3/4 view. Using a fixed angle (rather than a fraction of vertical extent)
    # ensures the camera angle is consistent regardless of object proportions.
    elevation = radius * np.tan(np.radians(_CAMERA_ELEVATION_DEG))

    logger.info(
        f"Camera framing: focal={focal_point}, radius={radius:.2f}, "
        f"elevation={elevation:.2f}, focus_extents=({dx:.2f}, {dy:.2f}, {dz:.2f})"
    )

    return focal_point, radius, elevation
