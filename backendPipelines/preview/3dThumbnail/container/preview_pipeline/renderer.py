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

# Orbit angle of a still preview: the front three-quarter view. Orbit frame i is at
# i * 360 / n_frames degrees, so the middle frame of an orbit shows the back of the model.
STILL_VIEW_DEG = 45.0


def is_point_cloud(pv_data) -> bool:
    """True when the data has no surface cells: no cells at all, or vertex cells only.

    A scene that mixes meshes and points holds both vertex and face cells, so it is not a point
    cloud and renders through the mesh path, points included.
    """
    n_cells = pv_data.n_cells
    if n_cells == 0:
        return True
    n_verts = getattr(pv_data, "n_verts", None)
    if isinstance(n_verts, (int, np.integer)):
        return int(n_verts) == n_cells
    return n_cells == pv_data.n_points


def still_view_frame_index(n_frames: int) -> int:
    """The orbit frame nearest the still-preview angle, for a still taken from an orbit."""
    if n_frames <= 1:
        return 0
    return int(np.floor(STILL_VIEW_DEG / (360.0 / n_frames) + 0.5)) % n_frames


def _resolve_full_bounds(pv_data, use_full_bounds):
    """Frame a mesh on its full bounds and a point cloud on its percentile bounds unless told.

    Percentile framing exists to ignore stray points in scans and splats; on a mesh it crops the
    coarser parts of a model whose vertices concentrate in a finely detailed region.
    """
    if use_full_bounds is None:
        return not is_point_cloud(pv_data)
    return bool(use_full_bounds)


def generate_rotating_frames(
    pv_data: pv.PolyData,
    n_frames: int = DEFAULT_N_FRAMES,
    resolution: tuple = DEFAULT_RESOLUTION,
    use_full_bounds=None,
) -> list:
    """
    Render a rotating sequence of frames around the Y-axis of the given PyVista data.
    Returns RGBA frames with transparent background. ``use_full_bounds`` None frames a mesh on its
    full bounds and a point cloud on its percentile bounds.
    """
    logger.info(f"Generating {n_frames} rotating frames at {resolution}")

    plotter = pv.Plotter(off_screen=True, window_size=resolution)
    plotter.set_background(_BG_COLOR)

    if is_point_cloud(pv_data):
        _add_point_cloud(plotter, pv_data)
    else:
        _add_mesh(plotter, pv_data)

    focal_point, radius, elevation = _compute_camera_framing(
        pv_data, use_full_bounds=_resolve_full_bounds(pv_data, use_full_bounds), resolution=resolution
    )

    # Compute stable clipping range from the actual max vertex distance
    # to the focal point. This is more accurate than the bounding box diagonal
    # because it accounts for corner vertices that are farther from center.
    # Using per-vertex distance ensures no geometry gets near-plane clipped
    # at any orbit angle (critical for USD models with tighter framing).
    max_extent = float(np.max(np.linalg.norm(pv_data.points - focal_point, axis=1)))

    frames = []
    for i in range(n_frames):
        angle_rad = np.radians(i * (360.0 / n_frames))

        cam_x = focal_point[0] + radius * np.cos(angle_rad)
        cam_z = focal_point[2] + radius * np.sin(angle_rad)
        cam_y = focal_point[1] + elevation

        cam_pos = np.array([cam_x, cam_y, cam_z])
        cam_distance = float(np.linalg.norm(cam_pos - focal_point))
        near_clip = max(cam_distance - max_extent * 1.1, cam_distance * 0.001)
        far_clip = cam_distance + max_extent * 1.5

        plotter.camera.position = (cam_x, cam_y, cam_z)
        plotter.camera.focal_point = tuple(focal_point)
        plotter.camera.up = (0.0, 1.0, 0.0)
        plotter.camera.clipping_range = (near_clip, far_clip)

        plotter.render()
        frames.append(_capture_rgba(plotter))

    plotter.close()
    logger.info(f"Generated {len(frames)} frames")
    return frames


def generate_static_frame(
    pv_data: pv.PolyData,
    resolution: tuple = DEFAULT_RESOLUTION,
    use_full_bounds=None,
) -> np.ndarray:
    """
    Render a single static frame of the given PyVista data from the front three-quarter view
    (STILL_VIEW_DEG). Returns RGBA image with transparent background.
    """
    logger.info(f"Generating static frame at {resolution}")

    plotter = pv.Plotter(off_screen=True, window_size=resolution)
    plotter.set_background(_BG_COLOR)

    if is_point_cloud(pv_data):
        _add_point_cloud(plotter, pv_data)
    else:
        _add_mesh(plotter, pv_data)

    focal_point, radius, elevation = _compute_camera_framing(
        pv_data, use_full_bounds=_resolve_full_bounds(pv_data, use_full_bounds), resolution=resolution
    )

    max_extent = float(np.max(np.linalg.norm(pv_data.points - focal_point, axis=1)))

    angle_rad = np.radians(STILL_VIEW_DEG)
    cam_x = focal_point[0] + radius * np.cos(angle_rad)
    cam_z = focal_point[2] + radius * np.sin(angle_rad)
    cam_y = focal_point[1] + elevation

    cam_pos = np.array([cam_x, cam_y, cam_z])
    cam_distance = float(np.linalg.norm(cam_pos - focal_point))
    near_clip = max(cam_distance - max_extent * 1.1, cam_distance * 0.001)
    far_clip = cam_distance + max_extent * 1.5

    plotter.camera.position = (cam_x, cam_y, cam_z)
    plotter.camera.focal_point = tuple(focal_point)
    plotter.camera.up = (0.0, 1.0, 0.0)
    plotter.camera.clipping_range = (near_clip, far_clip)

    plotter.render()
    img = _capture_rgba(plotter)
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
            point_size=POINT_SIZE,  # vertex cells of a scene that mixes meshes and points
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


def _capture_rgba(plotter) -> np.ndarray:
    """
    Read the rendered frame as RGBA with a transparent background.

    Colour and alpha come from the same render: the background is cleared with zero alpha, so a pixel
    is opaque exactly where geometry covers it and partially transparent where an anti-aliased edge
    covers it in part.
    """
    img = plotter.screenshot(transparent_background=True, return_img=True)
    return np.ascontiguousarray(img, dtype=np.uint8)


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
