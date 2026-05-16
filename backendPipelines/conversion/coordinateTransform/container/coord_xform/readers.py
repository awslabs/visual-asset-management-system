"""Point cloud file readers with chunked processing."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import pye57

from coord_xform.models import (
    Bounds,
    DatasetMetadata,
    InputFormat,
    PointChunk,
    ScanMetadata,
)

_MAGIC_BYTES = {
    b"ASTM-E57": InputFormat.E57,
    b"LASF": InputFormat.LAS,
    b"ply\n": InputFormat.PLY,
    b"ply\r": InputFormat.PLY,
}


def detect_format(path: Path) -> InputFormat:
    """Detect input format from magic bytes, falling back to extension."""
    if path.is_file():
        with open(path, "rb") as f:
            header = f.read(8)
        for magic, fmt in _MAGIC_BYTES.items():
            if header.startswith(magic):
                if fmt == InputFormat.LAS and path.suffix.lower() == ".laz":
                    return InputFormat.LAZ
                return fmt

    suffix = path.suffix.lower()
    match suffix:
        case ".e57":
            return InputFormat.E57
        case ".las":
            return InputFormat.LAS
        case ".laz":
            return InputFormat.LAZ
        case ".ply":
            return InputFormat.PLY
        case _:
            raise ValueError(f"Unsupported file format: {suffix}")


class PointCloudReader(ABC):
    """Base class for point cloud file readers."""

    @abstractmethod
    def read_metadata(self, path: Path) -> DatasetMetadata:
        """Read file metadata without loading point data."""

    @abstractmethod
    def read_chunks(
        self, path: Path, chunk_size: int
    ) -> Iterator[PointChunk]:
        """Yield point data in chunks for memory-efficient processing."""


class E57Reader(PointCloudReader):
    """Reader for ASTM E2807 E57 files."""

    def read_metadata(self, path: Path) -> DatasetMetadata:
        """Read E57 file metadata."""
        import pye57

        e57 = pye57.E57(str(path))
        header = e57.get_header(0)
        point_count = sum(
            e57.get_header(i).point_count for i in range(e57.scan_count)
        )

        crs = None
        if hasattr(header, "coordinate_metadata"):
            crs = header.coordinate_metadata

        return DatasetMetadata(
            file_path=path,
            format=InputFormat.E57,
            crs=crs,
            point_count=point_count,
            scan_count=e57.scan_count,
        )

    def _extract_scan_metadata(
        self, e57_file: pye57.E57, scan_idx: int
    ) -> ScanMetadata:
        """Extract metadata from an E57 scan header."""
        header = e57_file.get_header(scan_idx)
        return ScanMetadata(
            scan_index=scan_idx,
            name=getattr(header, "name", None),
            guid=getattr(header, "guid", None),
            timestamp=getattr(header, "acquisition_start", None),
            sensor_model=getattr(header, "sensor_model", None),
            sensor_serial=getattr(header, "sensor_serial_number", None),
            temperature=getattr(header, "temperature", None),
            humidity=getattr(header, "relative_humidity", None),
        )

    def read_chunks(
        self, path: Path, chunk_size: int
    ) -> Iterator[PointChunk]:
        """Yield point data from E57 in chunks."""
        import numpy as np
        import pye57

        e57 = pye57.E57(str(path))

        for scan_idx in range(e57.scan_count):
            scan_meta = self._extract_scan_metadata(e57, scan_idx)
            data = e57.read_scan_raw(scan_idx)

            xyz = np.column_stack([
                data["cartesianX"],
                data["cartesianY"],
                data["cartesianZ"],
            ]).astype(np.float64)

            intensity = None
            if "intensity" in data:
                intensity = np.asarray(
                    data["intensity"], dtype=np.float32
                )

            rgb = None
            if all(k in data for k in ("colorRed", "colorGreen", "colorBlue")):
                rgb = np.column_stack([
                    data["colorRed"],
                    data["colorGreen"],
                    data["colorBlue"],
                ]).astype(np.uint8)

            total_points = xyz.shape[0]
            for chunk_idx, start in enumerate(
                range(0, total_points, chunk_size)
            ):
                end = min(start + chunk_size, total_points)
                chunk = PointChunk(
                    xyz=xyz[start:end],
                    intensity=(
                        intensity[start:end]
                        if intensity is not None
                        else None
                    ),
                    rgb=rgb[start:end] if rgb is not None else None,
                    scan_index=scan_idx,
                    chunk_index=chunk_idx,
                    scan_metadata=scan_meta,
                )
                yield chunk


class LasReader(PointCloudReader):
    """Reader for LAS/LAZ files."""

    def read_metadata(self, path: Path) -> DatasetMetadata:
        """Read LAS/LAZ file metadata."""
        import laspy

        with laspy.open(str(path)) as f:
            header = f.header
            point_count = header.point_count

            crs = None
            for vlr in header.vlrs:
                if vlr.record_id == 2112:
                    crs = vlr.string
                    break

            fmt = (
                InputFormat.LAZ
                if path.suffix.lower() == ".laz"
                else InputFormat.LAS
            )

            return DatasetMetadata(
                file_path=path,
                format=fmt,
                crs=crs,
                point_count=point_count,
                scan_count=1,
            )

    def read_chunks(
        self, path: Path, chunk_size: int
    ) -> Iterator[PointChunk]:
        """Yield point data from LAS/LAZ in chunks."""
        import laspy
        import numpy as np

        with laspy.open(str(path)) as f:
            for chunk_idx, points in enumerate(
                f.chunk_iterator(chunk_size)
            ):
                xyz = np.column_stack([
                    points.x, points.y, points.z
                ]).astype(np.float64)

                intensity = None
                if hasattr(points, "intensity"):
                    intensity = np.asarray(
                        points.intensity, dtype=np.float32
                    )

                rgb = None
                if hasattr(points, "red"):
                    rgb = np.column_stack([
                        np.asarray(points.red) >> 8,
                        np.asarray(points.green) >> 8,
                        np.asarray(points.blue) >> 8,
                    ]).astype(np.uint8)

                classification = None
                if hasattr(points, "classification"):
                    classification = np.asarray(
                        points.classification, dtype=np.uint8
                    )

                yield PointChunk(
                    xyz=xyz,
                    intensity=intensity,
                    rgb=rgb,
                    classification=classification,
                    scan_index=0,
                    chunk_index=chunk_idx,
                )


class PlyReader(PointCloudReader):
    """Reader for PLY format point clouds."""

    def read_metadata(self, path: Path) -> DatasetMetadata:
        """Read PLY file metadata."""
        import open3d as o3d

        pcd = o3d.io.read_point_cloud(str(path))
        points = np.asarray(pcd.points)
        point_count = points.shape[0]

        bounds = None
        if point_count > 0:
            bounds = Bounds(
                min_x=float(points[:, 0].min()),
                min_y=float(points[:, 1].min()),
                min_z=float(points[:, 2].min()),
                max_x=float(points[:, 0].max()),
                max_y=float(points[:, 1].max()),
                max_z=float(points[:, 2].max()),
            )

        return DatasetMetadata(
            file_path=path,
            format=InputFormat.PLY,
            crs=None,
            point_count=point_count,
            scan_count=1,
            bounds=bounds,
        )

    def read_chunks(
        self, path: Path, chunk_size: int
    ) -> Iterator[PointChunk]:
        """Yield point data from PLY in chunks."""
        import open3d as o3d

        pcd = o3d.io.read_point_cloud(str(path))
        xyz = np.asarray(pcd.points, dtype=np.float64)

        rgb = None
        if pcd.has_colors():
            rgb = (np.asarray(pcd.colors) * 255).astype(np.uint8)

        normals = None
        if pcd.has_normals():
            normals = np.asarray(pcd.normals, dtype=np.float32)

        total_points = xyz.shape[0]
        for chunk_idx, start in enumerate(range(0, total_points, chunk_size)):
            end = min(start + chunk_size, total_points)
            yield PointChunk(
                xyz=xyz[start:end],
                rgb=rgb[start:end] if rgb is not None else None,
                normals=normals[start:end] if normals is not None else None,
                scan_index=0,
                chunk_index=chunk_idx,
            )


def get_reader(fmt: InputFormat) -> PointCloudReader:
    """Get the appropriate reader for a file format."""
    match fmt:
        case InputFormat.E57:
            return E57Reader()
        case InputFormat.LAS | InputFormat.LAZ:
            return LasReader()
        case InputFormat.PLY:
            return PlyReader()
