"""A scan's transformed points, held on the container's ephemeral disk rather than in memory."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import numpy as np

from coord_xform.models import PointChunk, ScanMetadata

# The optional per-point arrays, in the order they are appended to the spill file. The order is part of
# the file format: a chunk's record says which of them were present, and the reader replays them in this
# same sequence, so reordering this tuple silently reinterprets every existing chunk.
_OPTIONAL_FIELDS: tuple[tuple[str, np.dtype, int], ...] = (
    ("intensity", np.dtype(np.float32), 1),
    ("rgb", np.dtype(np.uint8), 3),
    ("normals", np.dtype(np.float32), 3),
    ("classification", np.dtype(np.uint8), 1),
)

_XYZ_DTYPE = np.dtype(np.float64)

# Bytes one point occupies in the spill when every field is present: xyz 3x8, intensity 4, rgb 3,
# normals 3x4, classification 1. Reported by `bytes_per_point` so the caller can size a disk budget
# without knowing the layout.
SPILL_BYTES_PER_POINT_MAX = 3 * 8 + 4 + 3 + 3 * 4 + 1


class ChunkSpill:
    """One scan's transformed chunks, appended to a file and read back one chunk at a time.

    Peak memory over a whole scan is one chunk, not one cloud. The per-axis minimum and maximum are
    accumulated as chunks arrive, so ``header.offsets`` and ``header.scales`` are derivable before the
    first point is written and without a second pass. Those values are elementwise identical to
    ``np.min`` / ``np.max`` over the concatenated array, so an output written from a spill carries the
    same header and the same encoded coordinates as one written from a list.

    ``chunks()`` reopens the file, so it can be iterated once per output format.
    """

    def __init__(self, directory: Path, scan_index: int) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        fd, path = tempfile.mkstemp(
            prefix=f"scan{scan_index:03d}_", suffix=".spill", dir=directory
        )
        os.close(fd)
        self._path = Path(path)
        # The handle is owned for the object's lifetime, not for this call: append() writes to it
        # repeatedly and close() (line ~142) closes it and clears the attribute. The class is also a
        # context manager (__enter__/__exit__), which is how every caller acquires it, so the handle is
        # released on the exception path too. chunks() reopens the file for reading afterwards.
        # nosemgrep: open-never-closed
        self._handle = open(self._path, "wb")
        # (point_count, present-field names) per appended chunk. A few tens of bytes per chunk, so a
        # cloud spilled at the default chunk size costs well under a megabyte of index.
        self._records: list[tuple[int, tuple[str, ...]]] = []
        self._scan_index = scan_index
        self._point_count = 0
        self._min_xyz: np.ndarray | None = None
        self._max_xyz: np.ndarray | None = None
        self._scan_metadata: ScanMetadata | None = None

    # -- properties the writers need before they open their output ----------------------------------

    @property
    def scan_index(self) -> int:
        return self._scan_index

    @property
    def point_count(self) -> int:
        return self._point_count

    @property
    def is_empty(self) -> bool:
        return self._point_count == 0

    @property
    def scan_metadata(self) -> ScanMetadata | None:
        return self._scan_metadata

    @property
    def min_xyz(self) -> np.ndarray:
        """Per-axis minimum over every appended point."""
        if self._min_xyz is None:
            raise ValueError("an empty spill has no extent")
        return self._min_xyz

    @property
    def max_xyz(self) -> np.ndarray:
        """Per-axis maximum over every appended point."""
        if self._max_xyz is None:
            raise ValueError("an empty spill has no extent")
        return self._max_xyz

    def has_field(self, name: str) -> bool:
        """Whether any appended chunk carried `name`."""
        return any(name in present for _count, present in self._records)

    def bytes_per_point(self) -> int:
        """Bytes one point occupies in this spill, given the fields its chunks actually carry."""
        total = _XYZ_DTYPE.itemsize * 3
        for name, dtype, width in _OPTIONAL_FIELDS:
            if self.has_field(name):
                total += dtype.itemsize * width
        return total

    # -- writing -----------------------------------------------------------------------------------

    def append(self, chunk: PointChunk) -> None:
        """Write one transformed chunk to disk and fold its extent into the running min/max."""
        if self._handle is None:
            raise ValueError("this spill is closed; nothing more can be appended")
        count = chunk.count
        if count == 0:
            return

        xyz = np.ascontiguousarray(chunk.xyz, dtype=_XYZ_DTYPE)
        self._handle.write(xyz.tobytes())

        chunk_min = xyz.min(axis=0)
        chunk_max = xyz.max(axis=0)
        self._min_xyz = (
            chunk_min if self._min_xyz is None else np.minimum(self._min_xyz, chunk_min)
        )
        self._max_xyz = (
            chunk_max if self._max_xyz is None else np.maximum(self._max_xyz, chunk_max)
        )

        present: list[str] = []
        for name, dtype, width in _OPTIONAL_FIELDS:
            value = getattr(chunk, name, None)
            if value is None:
                continue
            shape = (count,) if width == 1 else (count, width)
            array = np.ascontiguousarray(value, dtype=dtype).reshape(shape)
            self._handle.write(array.tobytes())
            present.append(name)

        self._records.append((count, tuple(present)))
        self._point_count += count
        if self._scan_metadata is None and chunk.scan_metadata is not None:
            self._scan_metadata = chunk.scan_metadata

    def close(self) -> None:
        """Finish writing. Reading is only defined once this has been called."""
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def discard(self) -> None:
        """Close and delete the spill file."""
        self.close()
        self._path.unlink(missing_ok=True)

    # -- reading -----------------------------------------------------------------------------------

    def chunks(self) -> Iterator[PointChunk]:
        """Replay the appended chunks, one at a time, in the order they were written."""
        if self._handle is not None:
            raise ValueError("close the spill before reading it back")
        with open(self._path, "rb") as f:
            for chunk_index, (count, present) in enumerate(self._records):
                xyz = np.frombuffer(
                    f.read(_XYZ_DTYPE.itemsize * 3 * count), dtype=_XYZ_DTYPE
                ).reshape(count, 3)
                values: dict[str, np.ndarray] = {}
                for name, dtype, width in _OPTIONAL_FIELDS:
                    if name not in present:
                        continue
                    raw = f.read(dtype.itemsize * width * count)
                    array = np.frombuffer(raw, dtype=dtype)
                    values[name] = array if width == 1 else array.reshape(count, width)
                yield PointChunk(
                    xyz=xyz,
                    scan_index=self._scan_index,
                    chunk_index=chunk_index,
                    scan_metadata=self._scan_metadata,
                    **values,
                )

    def __enter__(self) -> ChunkSpill:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.discard()
