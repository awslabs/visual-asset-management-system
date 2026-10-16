# Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Point cloud format handler for .las, .laz, .e57, .ptx, .pcd files.
Loads point cloud data, bounded to a point budget, and converts it to PyVista for rendering.

FARO scans (.fls, .fws) are not supported: reading them needs the proprietary FARO SDK, and Open3D,
which the handler used to pass them to, has no FARO reader, so every such run failed.
"""

import os
import numpy as np
import pyvista as pv
from ..utils.logging import get_logger
from . import point_streams

logger = get_logger()

SUPPORTED_EXTENSIONS = {".las", ".laz", ".e57", ".ptx", ".pcd"}

# Line-oriented formats read as a byte stream: in S3 mode straight from the object, with no local copy.
STREAMED_EXTENSIONS = {".ptx"}

# Default point budget (the run passes the budget derived from its task's resources).
MAX_POINTS_FOR_RENDER = 20_000_000

# Points decoded per streaming iteration for LAS/LAZ. This is the memory floor of the read: one
# chunk's decoded coordinates (and colours) are resident at a time, independent of file size.
_LAS_CHUNK_POINTS = 2_000_000


def can_handle(extension: str) -> bool:
    return extension.lower() in SUPPORTED_EXTENSIONS


def is_streamed(extension: str) -> bool:
    return extension.lower() in STREAMED_EXTENSIONS


def load(file_path: str, max_points: int = MAX_POINTS_FOR_RENDER) -> pv.PolyData:
    """
    Load a point cloud file and return a PyVista PolyData object of at most ``max_points`` points.
    Supports: .las, .laz, .e57, .ptx, .pcd
    """
    ext = os.path.splitext(file_path)[1].lower()
    logger.info(f"Loading point cloud file: {file_path} (format: {ext}, budget {max_points:,} points)")

    if ext in STREAMED_EXTENSIONS:
        return load_stream(point_streams.iter_file_bytes(file_path), ext, max_points)
    if ext in (".las", ".laz"):
        points, colors = _load_las(file_path, max_points=max_points)
    elif ext == ".e57":
        points, colors = point_streams.read_e57(file_path, max_points)
    elif ext == ".pcd":
        points, colors = _load_pcd(file_path)
    else:
        raise ValueError(f"Unsupported point cloud format: {ext}")
    return _to_pyvista(points, colors, max_points)


def load_stream(chunks, ext: str, max_points: int = MAX_POINTS_FOR_RENDER) -> pv.PolyData:
    """Load a line-oriented point cloud from an iterator of byte chunks (a local file or an S3 stream)."""
    ext = ext.lower()
    if ext == ".ptx":
        points, colors = point_streams.read_ptx(chunks, max_points)
    else:
        raise ValueError(f"Unsupported streamed point cloud format: {ext}")
    return _to_pyvista(points, colors, max_points)


def load_ply_points(file_path: str, max_points: int = MAX_POINTS_FOR_RENDER) -> pv.PolyData:
    """A vertex-only PLY (a point cloud), sampled to ``max_points``.

    A binary PLY is memory-mapped and sampled by row index, so only the kept rows are read. An ASCII
    PLY is streamed after its header in blocks of lines through the reservoir sampler.
    """
    from . import ply_header

    header = ply_header.read_header(file_path)
    vertex = header.element("vertex")
    if vertex is None or vertex.count == 0:
        raise ValueError("PLY file has no vertices")
    names = [name for name, _ in vertex.properties]
    colour_names = next((group for group in (("red", "green", "blue"), ("r", "g", "b"),
                                             ("diffuse_red", "diffuse_green", "diffuse_blue"))
                         if set(group) <= set(names)), None)
    if header.format != "ascii":
        rows = ply_header.vertex_memmap(file_path, header)
        if len(rows) > max_points:
            keep = np.sort(np.random.default_rng(42).choice(len(rows), size=max_points, replace=False))
            rows = rows[keep]
        points = np.column_stack([rows["x"], rows["y"], rows["z"]]).astype(np.float64)
        colors = None
        if colour_names:
            values = np.column_stack([rows[name] for name in colour_names])
            colors = _to_uint8_colours(values)
        return _to_pyvista(points, colors, max_points)

    if header.elements[0] is not vertex or vertex.has_list:
        raise ValueError("ASCII PLY point cloud must start with a list-free vertex element")
    columns = [names.index(axis) for axis in ("x", "y", "z")]
    colour_columns = [names.index(name) for name in colour_names] if colour_names else None
    sampler = point_streams.ReservoirSampler(max_points)
    chunks = point_streams.iter_file_bytes(file_path)
    lines = point_streams.LineReader(chunks)
    # Skip the header, then read exactly the vertex element's lines.
    while True:
        line = lines.next_line()
        if line is None or line.strip() == b"end_header":
            break
    remaining = vertex.count
    while remaining > 0:
        block, count = lines.take_lines(min(remaining, point_streams.TEXT_CHUNK_LINES))
        if count == 0:
            break
        remaining -= count
        values = point_streams.parse_number_block(block, len(names))
        if not len(values):
            continue
        colors = _to_uint8_colours(values[:, colour_columns]) if colour_columns else None
        sampler.add(values[:, columns], colors)
    points, colors = sampler.result()
    if not len(points):
        raise ValueError("No points found in PLY file")
    return _to_pyvista(points, colors, max_points)


def _to_uint8_colours(values) -> np.ndarray:
    values = np.asarray(values)
    if values.dtype.kind == "f":
        peak = 1.0 if float(np.nanmax(values, initial=0.0)) <= 1.0 else 255.0
        return np.clip(values / peak * 255.0 + 0.5, 0, 255).astype(np.uint8)
    peak = 65535.0 if int(values.max(initial=0)) > 255 else 255.0
    return np.clip(values.astype(np.float64) / peak * 255.0 + 0.5, 0, 255).astype(np.uint8)


def _to_pyvista(points, colors, max_points: int) -> pv.PolyData:
    if len(points) > max_points:
        logger.info(f"Downsampling from {len(points)} to {max_points} points")
        indices = np.random.default_rng(42).choice(len(points), size=max_points, replace=False)
        points = points[indices]
        if colors is not None:
            colors = colors[indices]

    pv_cloud = pv.PolyData(np.ascontiguousarray(points, dtype=np.float64))

    if colors is not None and len(colors) > 0:
        pv_cloud.point_data["RGB"] = np.ascontiguousarray(colors, dtype=np.uint8)

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
