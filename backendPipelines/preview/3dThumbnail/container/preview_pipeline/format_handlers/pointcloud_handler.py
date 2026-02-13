# Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Point cloud format handler for .las, .laz, .e57, .ptx, .fls, .fws, .pcd files.
Loads point cloud data and converts to PyVista for rendering.
"""

import os
import numpy as np
import pyvista as pv
from ..utils.logging import get_logger

logger = get_logger()

SUPPORTED_EXTENSIONS = {".las", ".laz", ".e57", ".ptx", ".pcd", ".fls", ".fws"}

# Maximum points to render for performance (downsample if exceeded)
MAX_POINTS_FOR_RENDER = 20_000_000


def can_handle(extension: str) -> bool:
    return extension.lower() in SUPPORTED_EXTENSIONS


def load(file_path: str) -> pv.PolyData:
    """
    Load a point cloud file and return a PyVista PolyData object for rendering.
    Supports: .las, .laz, .e57, .ptx, .pcd, .fls, .fws
    """
    ext = os.path.splitext(file_path)[1].lower()
    logger.info(f"Loading point cloud file: {file_path} (format: {ext})")

    if ext in (".las", ".laz"):
        points, colors = _load_las(file_path)
    elif ext == ".e57":
        points, colors = _load_e57(file_path)
    elif ext == ".ptx":
        points, colors = _load_ptx(file_path)
    elif ext == ".pcd":
        points, colors = _load_pcd(file_path)
    elif ext in (".fls", ".fws"):
        points, colors = _load_faro(file_path)
    else:
        raise ValueError(f"Unsupported point cloud format: {ext}")

    # Downsample if needed
    if len(points) > MAX_POINTS_FOR_RENDER:
        logger.info(f"Downsampling from {len(points)} to {MAX_POINTS_FOR_RENDER} points")
        indices = np.random.default_rng(42).choice(
            len(points), size=MAX_POINTS_FOR_RENDER, replace=False
        )
        points = points[indices]
        if colors is not None:
            colors = colors[indices]

    pv_cloud = pv.PolyData(points)

    if colors is not None and len(colors) > 0:
        pv_cloud.point_data["RGB"] = colors

    logger.info(f"Loaded point cloud: {len(points)} points")
    return pv_cloud


def _load_las(file_path: str):
    """Load LAS/LAZ files using laspy."""
    import laspy

    las = laspy.read(file_path)
    points = np.vstack([las.x, las.y, las.z]).T.astype(np.float64)

    # Try to extract colors
    colors = None
    try:
        if hasattr(las, 'red') and hasattr(las, 'green') and hasattr(las, 'blue'):
            r = np.array(las.red, dtype=np.float64)
            g = np.array(las.green, dtype=np.float64)
            b = np.array(las.blue, dtype=np.float64)

            # LAS colors are often 16-bit, normalize to 0-255
            max_val = max(r.max(), g.max(), b.max(), 1)
            if max_val > 255:
                r = (r / max_val * 255).astype(np.uint8)
                g = (g / max_val * 255).astype(np.uint8)
                b = (b / max_val * 255).astype(np.uint8)
            else:
                r = r.astype(np.uint8)
                g = g.astype(np.uint8)
                b = b.astype(np.uint8)

            colors = np.column_stack([r, g, b])
    except Exception as e:
        logger.warning(f"Could not extract colors from LAS: {e}")

    return points, colors


def _load_e57(file_path: str):
    """Load E57 files using pye57."""
    import pye57

    e57 = pye57.E57(file_path)
    header = e57.get_header(0)
    raw_data = e57.read_scan_raw(0)

    x = np.array(raw_data["cartesianX"], dtype=np.float64)
    y = np.array(raw_data["cartesianY"], dtype=np.float64)
    z = np.array(raw_data["cartesianZ"], dtype=np.float64)
    points = np.column_stack([x, y, z])

    colors = None
    try:
        if "colorRed" in raw_data and "colorGreen" in raw_data and "colorBlue" in raw_data:
            r = np.array(raw_data["colorRed"], dtype=np.uint8)
            g = np.array(raw_data["colorGreen"], dtype=np.uint8)
            b = np.array(raw_data["colorBlue"], dtype=np.uint8)
            colors = np.column_stack([r, g, b])
    except Exception as e:
        logger.warning(f"Could not extract colors from E57: {e}")

    return points, colors


def _load_ptx(file_path: str):
    """
    Load PTX files (Leica structured text format).
    PTX is a simple text-based point cloud format with optional intensity and color.
    """
    points_list = []
    colors_list = []

    with open(file_path, 'r') as f:
        # PTX header format (10 lines total):
        #   Line 1: number of columns
        #   Line 2: number of rows
        #   Line 3: scanner origin (x y z)
        #   Lines 4-6: scanner axis vectors (3 lines of x y z)
        #   Lines 7-10: 4x4 transformation matrix (4 lines of 4 values)
        try:
            cols = int(f.readline().strip())
            rows = int(f.readline().strip())
        except ValueError:
            raise ValueError("Invalid PTX header")

        # Skip remaining 8 header lines (scanner origin, 3 axis vectors, 4x4 matrix)
        for _ in range(8):
            f.readline()

        # Read point data
        for line in f:
            parts = line.strip().split()
            if len(parts) < 3:
                continue
            try:
                x, y, z = float(parts[0]), float(parts[1]), float(parts[2])
                # Skip invalid points (some PTX files use 0 0 0 for invalid)
                if x == 0.0 and y == 0.0 and z == 0.0:
                    continue
                points_list.append([x, y, z])

                # Colors may be in columns 4-6 (after intensity) or 5-7
                if len(parts) >= 7:
                    r, g, b = int(parts[4]), int(parts[5]), int(parts[6])
                    colors_list.append([r, g, b])
            except (ValueError, IndexError):
                continue

    if not points_list:
        raise ValueError("No valid points found in PTX file")

    points = np.array(points_list, dtype=np.float64)
    colors = np.array(colors_list, dtype=np.uint8) if len(colors_list) == len(points_list) else None

    return points, colors


def _load_pcd(file_path: str):
    """Load PCD files using Open3D."""
    try:
        import open3d as o3d
    except ImportError:
        raise ImportError("Open3D is required for .pcd file support")

    pcd = o3d.io.read_point_cloud(file_path)
    points = np.asarray(pcd.points, dtype=np.float64)

    colors = None
    if pcd.has_colors():
        # Open3D colors are 0.0-1.0 float, convert to 0-255 uint8
        colors = (np.asarray(pcd.colors) * 255).astype(np.uint8)

    return points, colors


def _load_faro(file_path: str):
    """
    Load FARO scanner formats (.fls, .fws).
    Attempts to use Open3D. If unsupported, raises a descriptive error.
    """
    try:
        import open3d as o3d
    except ImportError:
        raise ImportError("Open3D is required for FARO format support")

    ext = os.path.splitext(file_path)[1].lower()
    logger.info(f"Attempting to load FARO format {ext} via Open3D...")

    try:
        pcd = o3d.io.read_point_cloud(file_path)
        points = np.asarray(pcd.points, dtype=np.float64)

        if len(points) == 0:
            raise ValueError(f"No points loaded from FARO file: {file_path}")

        colors = None
        if pcd.has_colors():
            colors = (np.asarray(pcd.colors) * 255).astype(np.uint8)

        return points, colors
    except Exception as e:
        raise ValueError(
            f"Unable to load FARO {ext} format. "
            f"This format may require proprietary conversion. Error: {e}"
        )
