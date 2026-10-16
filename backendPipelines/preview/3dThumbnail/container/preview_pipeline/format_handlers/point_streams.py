# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Bounded point-cloud readers: every reader here holds at most one chunk of the input plus a sample of
at most the point budget, whatever the input's size.

* ``ReservoirSampler`` keeps a uniform random sample of a stream of unknown length.
* ``read_e57`` reads every scan of an E57 file in fixed-size buffers through the libe57 reader,
  applies each scan's pose and scales colours by the scan's declared colour limits.
* ``read_ptx`` parses a PTX byte stream scan by scan: each scan's ten header lines (columns, rows,
  scanner origin and axes, 4x4 transform) are read and the transform is applied to that scan's points.
  The input arrives as byte chunks, so it can be streamed straight from Amazon S3.
"""

import io
from typing import Iterable, Iterator, List, Optional, Tuple

import numpy as np

from ..utils.logging import get_logger

logger = get_logger()

E57_CHUNK_POINTS = 1_000_000
TEXT_CHUNK_LINES = 1_000_000
LOCAL_READ_BYTES = 16 * 1024 ** 2


# --------------------------------------------------------------------------------------------------
# Sampling
# --------------------------------------------------------------------------------------------------

class ReservoirSampler:
    """A uniform random sample of at most ``capacity`` points from chunks of unknown total count.

    Algorithm R, vectorised per chunk: the first ``capacity`` points fill the reservoir; the point
    with global index ``i`` then replaces slot ``j ~ U[0, i]`` when ``j < capacity``. Storage grows
    with the points kept, so a small input never allocates the full budget. Colours are kept only
    when every chunk carries them.
    """

    def __init__(self, capacity: int, seed: int = 42):
        self.capacity = max(1, int(capacity))
        self.seen = 0
        self._filled = 0
        self._points = np.empty((0, 3), dtype=np.float64)
        self._colors: Optional[np.ndarray] = np.empty((0, 3), dtype=np.uint8)
        self._rng = np.random.default_rng(seed)

    def _grow(self, needed: int):
        if needed <= len(self._points):
            return
        size = min(self.capacity, max(needed, 2 * len(self._points), 65_536))
        points = np.empty((size, 3), dtype=np.float64)
        points[:self._filled] = self._points[:self._filled]
        self._points = points
        if self._colors is not None:
            colors = np.empty((size, 3), dtype=np.uint8)
            colors[:self._filled] = self._colors[:self._filled]
            self._colors = colors

    def add(self, points: np.ndarray, colors: Optional[np.ndarray] = None):
        count = len(points)
        if count == 0:
            return
        if colors is None or len(colors) != count:
            self._colors = None
        fill = min(count, self.capacity - self._filled)
        if fill:
            self._grow(self._filled + fill)
            self._points[self._filled:self._filled + fill] = points[:fill]
            if self._colors is not None:
                self._colors[self._filled:self._filled + fill] = colors[:fill]
            self._filled += fill
        if fill < count:
            global_index = np.arange(self.seen + fill, self.seen + count, dtype=np.float64)
            slots = (self._rng.random(count - fill) * (global_index + 1)).astype(np.int64)
            keep = slots < self.capacity
            self._points[slots[keep]] = points[fill:][keep]
            if self._colors is not None:
                self._colors[slots[keep]] = colors[fill:][keep]
        self.seen += count

    def result(self) -> Tuple[np.ndarray, Optional[np.ndarray]]:
        points = self._points[:self._filled]
        colors = None if self._colors is None else self._colors[:self._filled]
        if self.seen > self._filled:
            logger.info(f"Sampled {self._filled:,} of {self.seen:,} points (budget {self.capacity:,})")
        return points, colors


# --------------------------------------------------------------------------------------------------
# E57
# --------------------------------------------------------------------------------------------------

_E57_CARTESIAN = ("cartesianX", "cartesianY", "cartesianZ")
_E57_SPHERICAL = ("sphericalRange", "sphericalAzimuth", "sphericalElevation")
_E57_COLOURS = ("colorRed", "colorGreen", "colorBlue")


def _e57_colour_limits(header, prototype) -> List[Tuple[float, float]]:
    """(minimum, maximum) per colour channel: the scan's ``colorLimits``, else the bounds of the
    channel's integer type in the point prototype, else 0..255."""
    from pye57 import libe57

    limits = []
    for channel in ("Red", "Green", "Blue"):
        bounds = None
        try:
            if header.node.isDefined("colorLimits"):
                node = header["colorLimits"]
                bounds = (float(node[f"color{channel}Minimum"].value()),
                          float(node[f"color{channel}Maximum"].value()))
        except Exception:
            bounds = None
        if bounds is None:
            try:
                field = prototype.get(f"color{channel}")
                for cast in (libe57.IntegerNode, libe57.ScaledIntegerNode, libe57.FloatNode):
                    try:
                        typed = cast(field)
                        bounds = (float(typed.minimum()), float(typed.maximum()))
                        break
                    except Exception:
                        continue
            except Exception:
                bounds = None
        if bounds is None or not np.isfinite(bounds).all() or bounds[1] <= bounds[0]:
            bounds = (0.0, 255.0)
        limits.append(bounds)
    return limits


def scale_colours(values: np.ndarray, limits: List[Tuple[float, float]]) -> np.ndarray:
    """Map an (n, 3) array of colour values from their declared limits to uint8."""
    low = np.array([lo for lo, _ in limits], dtype=np.float64)
    span = np.array([hi - lo for lo, hi in limits], dtype=np.float64)
    scaled = (np.asarray(values, dtype=np.float64) - low) / span * 255.0
    return np.clip(scaled + 0.5, 0, 255).astype(np.uint8)


def read_e57(file_path: str, max_points: int, chunk_points: int = E57_CHUNK_POINTS):
    """Every scan of an E57 file, posed into the file's common frame, sampled to ``max_points``.

    Each scan is read through one libe57 reader into fixed buffers of ``chunk_points`` records, so
    peak memory is the buffers plus the sample. Points flagged invalid are dropped; spherical scans
    are converted to Cartesian before the pose is applied.
    """
    import pye57
    from pye57 import libe57

    e57 = pye57.E57(file_path)
    sampler = ReservoirSampler(max_points)
    try:
        for index in range(e57.scan_count):
            header = e57.get_header(index)
            fields = set(header.point_fields)
            if set(_E57_CARTESIAN) <= fields:
                coordinates, invalid = _E57_CARTESIAN, "cartesianInvalidState"
            elif set(_E57_SPHERICAL) <= fields:
                coordinates, invalid = _E57_SPHERICAL, "sphericalInvalidState"
            else:
                logger.warning(f"E57 scan {index} has no Cartesian or spherical coordinates; skipped")
                continue
            names = list(coordinates)
            with_colour = set(_E57_COLOURS) <= fields
            if with_colour:
                names += list(_E57_COLOURS)
            if invalid in fields:
                names.append(invalid)

            arrays = {}
            buffers = libe57.VectorSourceDestBuffer()
            for name in names:
                dtype = np.uint8 if name == invalid else np.float64
                arrays[name] = np.empty(chunk_points, dtype=dtype)
                buffers.append(libe57.SourceDestBuffer(
                    e57.image_file, name, arrays[name], chunk_points, True, True))

            prototype = libe57.StructureNode(header.points.prototype())
            limits = _e57_colour_limits(header, prototype) if with_colour else None
            rotation = np.asarray(header.rotation_matrix, dtype=np.float64)
            translation = np.asarray(header.translation, dtype=np.float64)
            logger.info(f"E57 scan {index}: {header.point_count:,} points, pose "
                        f"{'set' if header.has_pose() else 'identity'}, colour "
                        f"{'limits ' + str(limits) if with_colour else 'none'}")

            reader = header.points.reader(buffers)
            try:
                while True:
                    count = reader.read()
                    if count <= 0:
                        break
                    a, b, c = (arrays[name][:count] for name in coordinates)
                    if coordinates is _E57_SPHERICAL:
                        cos_el = np.cos(c)
                        points = np.column_stack([a * cos_el * np.cos(b), a * cos_el * np.sin(b),
                                                  a * np.sin(c)])
                    else:
                        points = np.column_stack([a, b, c])
                    colors = None
                    if with_colour:
                        colors = scale_colours(np.column_stack(
                            [arrays[name][:count] for name in _E57_COLOURS]), limits)
                    if invalid in arrays:
                        valid = arrays[invalid][:count] == 0
                        points = points[valid]
                        colors = None if colors is None else colors[valid]
                    points = points @ rotation.T + translation
                    sampler.add(points, colors if with_colour else None)
            finally:
                reader.close()
    finally:
        e57.close()
    points, colors = sampler.result()
    if len(points) == 0:
        raise ValueError(f"E57 file contains no valid points: {file_path}")
    return points, colors


# --------------------------------------------------------------------------------------------------
# Line-oriented text
# --------------------------------------------------------------------------------------------------

def iter_file_bytes(file_path: str, chunk_bytes: int = LOCAL_READ_BYTES) -> Iterator[bytes]:
    with open(file_path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_bytes)
            if not chunk:
                return
            yield chunk


class LineReader:
    """Lines from an iterator of byte chunks, singly or in bulk blocks of whole lines."""

    def __init__(self, chunks: Iterable[bytes]):
        self._chunks = iter(chunks)
        self._buffer = b""
        self._eof = False

    def _fill(self) -> bool:
        if self._eof:
            return False
        try:
            chunk = next(self._chunks)
        except StopIteration:
            self._eof = True
            return False
        self._buffer += chunk
        return True

    def next_line(self) -> Optional[bytes]:
        """The next line without its newline, or None at the end of the stream."""
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                line, self._buffer = self._buffer[:newline], self._buffer[newline + 1:]
                return line.rstrip(b"\r")
            if not self._fill():
                if self._buffer:
                    line, self._buffer = self._buffer, b""
                    return line.rstrip(b"\r")
                return None

    def next_nonblank_line(self) -> Optional[bytes]:
        while True:
            line = self.next_line()
            if line is None or line.strip():
                return line

    def take_lines(self, limit: int) -> Tuple[bytes, int]:
        """A block of up to ``limit`` whole lines and the number of lines in it; (b"", 0) at the end."""
        while True:
            view = np.frombuffer(self._buffer, dtype=np.uint8)
            newlines = np.flatnonzero(view == 10)
            if len(newlines) >= limit or (len(newlines) and self._eof):
                count = min(limit, len(newlines))
                end = int(newlines[count - 1]) + 1
                block, self._buffer = self._buffer[:end], self._buffer[end:]
                return block, count
            if not self._fill():
                if len(newlines):
                    continue
                if self._buffer:
                    block, self._buffer = self._buffer, b""
                    return block, 1
                return b"", 0


def parse_number_block(block: bytes, min_columns: int) -> np.ndarray:
    """An (n, k) float array from a block of whitespace-separated number lines.

    The fast path parses the block in C (``np.loadtxt``). A block whose lines differ in column count
    or that holds a non-numeric line falls back to a per-line parse that keeps the lines with at least
    ``min_columns`` numbers, truncated to the shortest such line.
    """
    if not block.strip():
        return np.empty((0, min_columns))
    try:
        values = np.loadtxt(io.BytesIO(block), dtype=np.float64, ndmin=2)
        if values.shape[1] >= min_columns:
            return values
    except ValueError:
        pass
    rows = []
    for line in block.splitlines():
        try:
            row = [float(token) for token in line.split()]
        except ValueError:
            continue
        if len(row) >= min_columns:
            rows.append(row)
    if not rows:
        return np.empty((0, min_columns))
    width = min(len(row) for row in rows)
    return np.array([row[:width] for row in rows], dtype=np.float64)


def _floats(line: Optional[bytes], count: int) -> List[float]:
    if line is None:
        raise ValueError("Truncated PTX header")
    values = [float(token) for token in line.split()]
    if len(values) < count:
        raise ValueError(f"Invalid PTX header line: {line[:80]!r}")
    return values[:count]


def read_ptx(chunks: Iterable[bytes], max_points: int, chunk_lines: int = TEXT_CHUNK_LINES):
    """Every scan of a PTX stream, transformed by its header matrix, sampled to ``max_points``.

    A PTX file is a sequence of scans. Each opens with ten header lines -- columns, rows, the scanner
    origin, three scanner axes, and a 4x4 transform whose last row is the translation (points are row
    vectors) -- followed by ``columns x rows`` point lines ``x y z intensity [r g b]``. ``0 0 0``
    marks a missing return and is dropped before the transform.
    """
    lines = LineReader(chunks)
    sampler = ReservoirSampler(max_points)
    scans = 0
    while True:
        first = lines.next_nonblank_line()
        if first is None:
            break
        try:
            columns = int(float(first.split()[0]))
            rows = int(float(_floats(lines.next_line(), 1)[0]))
        except (ValueError, IndexError):
            if scans:
                logger.warning(f"Unreadable data after PTX scan {scans}; stopping there")
                break
            raise ValueError("Invalid PTX header")
        for _ in range(4):  # scanner origin and the three scanner axes
            _floats(lines.next_line(), 3)
        matrix = np.array([_floats(lines.next_line(), 4) for _ in range(4)], dtype=np.float64)
        remaining = columns * rows
        scans += 1
        logger.info(f"PTX scan {scans}: {columns} x {rows} = {remaining:,} points")
        while remaining > 0:
            block, count = lines.take_lines(min(remaining, chunk_lines))
            if count == 0:
                logger.warning(f"PTX scan {scans} ends {remaining:,} points early")
                break
            remaining -= count
            values = parse_number_block(block, 3)
            if not len(values):
                continue
            points = values[:, :3]
            valid = np.any(points != 0.0, axis=1)
            colors = None
            if values.shape[1] >= 7:
                colors = np.clip(values[valid, 4:7], 0, 255).astype(np.uint8)
            points = points[valid] @ matrix[:3, :3] + matrix[3, :3]
            sampler.add(points, colors)
    points, colors = sampler.result()
    if len(points) == 0:
        raise ValueError("No valid points found in PTX file")
    logger.info(f"PTX: {scans} scan(s), {sampler.seen:,} valid points")
    return points, colors
