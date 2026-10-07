# Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Point cloud format handler for .las, .laz, .e57, .ptx, .pcd, .xyz, .fls, .fws files.
Loads point cloud data (bounded to a point cap) and converts to PyVista for rendering.
"""

import os
import numpy as np
import pyvista as pv
from ..utils.logging import get_logger

logger = get_logger()

SUPPORTED_EXTENSIONS = {".las", ".laz", ".e57", ".ptx", ".pcd", ".xyz", ".fls", ".fws"}

# The library each extension is read with; ``none`` marks a format with no open reader.
LOADERS = {
    ".las": "laspy", ".laz": "laspy", ".e57": "pye57", ".ptx": "text", ".pcd": "pcd_reader",
    ".xyz": "text", ".fls": "none", ".fws": "none",
}

# Maximum points to render for performance (downsample if exceeded)
MAX_POINTS_FOR_RENDER = 20_000_000

# Points decoded per streaming iteration for LAS/LAZ. This is the memory floor of the read: one
# chunk's decoded coordinates (and colours) are resident at a time, independent of file size.
_LAS_CHUNK_POINTS = 2_000_000


def can_handle(extension: str) -> bool:
    return extension.lower() in SUPPORTED_EXTENSIONS


def load(file_path: str, max_points: int = MAX_POINTS_FOR_RENDER) -> pv.PolyData:
    """
    Load a point cloud file and return a PyVista PolyData object for rendering.
    Supports: .las, .laz, .e57, .ptx, .pcd, .xyz, .fls, .fws
    """
    ext = os.path.splitext(file_path)[1].lower()
    logger.info(f"Loading point cloud file: {file_path} (format: {ext})")
    points, colors = load_points(file_path, max_points)
    return to_polydata(points, colors)


def load_points(file_path: str, max_points: int = MAX_POINTS_FOR_RENDER):
    """``(points float64 (N, 3), colors uint8 (N, 3) or None)`` for a point-cloud file, bounded to
    ``max_points``. LAS/LAZ and XYZ are sampled while they are read; the other formats are loaded whole
    and then subsampled."""
    ext = os.path.splitext(file_path)[1].lower()

    if ext in (".las", ".laz"):
        points, colors = _load_las(file_path, max_points)
    elif ext == ".e57":
        points, colors = _load_e57(file_path)
    elif ext == ".ptx":
        points, colors = _load_ptx(file_path)
    elif ext == ".pcd":
        points, colors = _load_pcd(file_path)
    elif ext == ".xyz":
        points, colors = _load_xyz(file_path, max_points)
    elif ext in (".fls", ".fws"):
        points, colors = _load_faro(file_path)
    else:
        raise ValueError(f"Unsupported point cloud format: {ext}")

    return cap_points(points, colors, max_points)


def cap_points(points, colors, max_points: int):
    """Uniformly subsample (seeded) to at most ``max_points``, keeping colour rows with their points."""
    if len(points) > max_points:
        logger.info(f"Downsampling from {len(points)} to {max_points} points")
        indices = np.random.default_rng(42).choice(len(points), size=max_points, replace=False)
        points = points[indices]
        if colors is not None:
            colors = colors[indices]
    return points, colors


def to_polydata(points, colors) -> pv.PolyData:
    pv_cloud = pv.PolyData(points)
    if colors is not None and len(colors) > 0:
        pv_cloud.point_data["RGB"] = colors
    logger.info(f"Loaded point cloud: {len(points)} points")
    return pv_cloud


def _load_las(file_path: str, max_points: int = MAX_POINTS_FOR_RENDER):
    """Load LAS/LAZ with laspy, bounded to `max_points` WITHOUT materialising the whole cloud.

    `laspy.read()` decodes every point up front, and the previous shape then made two more full copies
    of the coordinates — `np.vstack` builds a (3, N) array and `.T.astype(np.float64)` copies it again
    because the transpose is not contiguous. Peak memory was therefore about three times the cloud's
    coordinate size regardless of the render cap, which was applied afterwards and so bounded render
    cost only. A cloud large enough to matter took the container down before it could be capped.

    Here the file is streamed in chunks and sampled as it is read, so peak memory is one chunk plus the
    bounded output, whatever the file's size. Sampling is a uniform stride rather than the random choice
    the caller applies to other formats: it is decidable from the header's point count alone, needs no
    second pass, and for a thumbnail an evenly spaced subset is at least as representative.
    """
    import laspy

    with laspy.open(file_path) as reader:
        total = int(reader.header.point_count)
        if total <= 0:
            raise ValueError(f"LAS/LAZ file contains no points: {file_path}")

        # Ceiling division, so the kept count never exceeds max_points.
        stride = 1 if total <= max_points else (total + max_points - 1) // max_points
        kept_total = (total + stride - 1) // stride
        if stride > 1:
            logger.info(
                f"Streaming {total} points with stride {stride} -> {kept_total} kept "
                f"(cap {max_points})")

        points = np.empty((kept_total, 3), dtype=np.float64)

        # Colours are accumulated at their stored 16-bit width and normalised at the end. Deciding the
        # 16-bit-vs-8-bit scale per chunk would band the output, because LAS stores 8-bit values in the
        # same uint16 fields and the deciding maximum is a property of the whole cloud; keeping only the
        # SAMPLED colours bounds this the same way the coordinates are bounded.
        point_format = reader.header.point_format
        has_colour = all(name in point_format.dimension_names for name in ("red", "green", "blue"))
        raw_colours = np.empty((kept_total, 3), dtype=np.uint16) if has_colour else None

        written = 0
        seen = 0
        for chunk in reader.chunk_iterator(_LAS_CHUNK_POINTS):
            count = len(chunk)
            # Phase the stride across chunk boundaries so the sample stays uniform over the whole file
            # rather than restarting within each chunk.
            start = (-seen) % stride
            if start < count:
                taken = len(range(start, count, stride))
                end = written + taken
                selection = slice(start, count, stride)
                points[written:end, 0] = chunk.x[selection]
                points[written:end, 1] = chunk.y[selection]
                points[written:end, 2] = chunk.z[selection]
                if raw_colours is not None:
                    try:
                        raw_colours[written:end, 0] = chunk.red[selection]
                        raw_colours[written:end, 1] = chunk.green[selection]
                        raw_colours[written:end, 2] = chunk.blue[selection]
                    except Exception as e:
                        logger.warning(f"Could not extract colors from LAS: {e}")
                        raw_colours = None
                written = end
            seen += count

        points = points[:written]

    colors = None
    if raw_colours is not None and written > 0:
        raw_colours = raw_colours[:written]
        max_val = max(int(raw_colours.max()), 1)
        if max_val > 255:
            colors = (raw_colours.astype(np.float32) / max_val * 255).astype(np.uint8)
        else:
            colors = raw_colours.astype(np.uint8)

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
    """Load PCD files (ascii, binary, binary_compressed) with the package's own reader."""
    from . import pcd_reader

    points, colors, header = pcd_reader.read_pcd(file_path)
    logger.info(f"PCD {header['data']} body: {header['points']} declared points, {len(points)} finite")
    return points, colors


def _load_xyz(file_path: str, max_points: int = MAX_POINTS_FOR_RENDER):
    """
    Load ASCII XYZ files: one point per line as ``x y z`` followed by optional columns, separated by
    whitespace, commas or semicolons; ``#`` starts a comment line. The file is sampled with a uniform
    stride while it is read, so a large file is bounded the way LAS/LAZ is. The last three columns are
    taken as an 8-bit colour only when every value is an integer in 0..255 and at least one exceeds 1,
    so unit normals and other float attributes are not mistaken for colour.
    """
    total = 0
    with open(file_path, "rb") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped and not stripped.startswith(b"#"):
                total += 1
    if total == 0:
        raise ValueError(f"No points found in XYZ file: {file_path}")

    stride = 1 if total <= max_points else (total + max_points - 1) // max_points
    coordinates = []
    colour_columns = []
    seen = 0
    with open(file_path, "r", errors="replace") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if seen % stride == 0:
                parts = stripped.replace(",", " ").replace(";", " ").split()
                if len(parts) >= 3:
                    try:
                        coordinates.append((float(parts[0]), float(parts[1]), float(parts[2])))
                        if len(parts) >= 6:
                            colour_columns.append((float(parts[-3]), float(parts[-2]), float(parts[-1])))
                    except ValueError:
                        pass
            seen += 1

    if not coordinates:
        raise ValueError(f"No valid points found in XYZ file: {file_path}")
    points = np.array(coordinates, dtype=np.float64).reshape(-1, 3)

    colors = None
    if colour_columns and len(colour_columns) == len(coordinates):
        candidate = np.array(colour_columns, dtype=np.float64)
        is_byte = (np.all((candidate >= 0) & (candidate <= 255))
                   and np.all(candidate == np.round(candidate))
                   and candidate.max() > 1.0)
        if is_byte:
            colors = candidate.astype(np.uint8)
    return points, colors


def _load_faro(file_path: str):
    """
    FARO scanner formats (.fls, .fws) have no open reader; the error names the conversion route.
    """
    ext = os.path.splitext(file_path)[1].lower()
    raise ValueError(
        f"Unable to load FARO {ext} format. This format requires proprietary conversion "
        f"(for example to E57 or LAS) before it can be rendered."
    )
