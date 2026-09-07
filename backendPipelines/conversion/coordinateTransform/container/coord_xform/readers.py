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

    def _read_crs(self, e57_file: pye57.E57) -> str | None:
        """The dataset CRS from the E57 root's `coordinateMetadata` string, or None when absent.

        ASTM E2807 records the coordinate reference system on the E57Root element. A scan header
        carries no CRS at all, so it cannot be reached through `get_header`. The field is optional and
        pye57's own writer seeds it with an empty string, so a blank value means "not recorded" rather
        than a CRS whose name is empty.
        """
        try:
            root = e57_file.root
            if not root.isDefined("coordinateMetadata"):
                return None
            value = root["coordinateMetadata"].value()
        except Exception:
            # An unreadable or absent root element is not a CRS mismatch; the rest of the metadata is
            # still usable, and `enforce_source_crs` is what decides whether an absent CRS blocks.
            return None

        text = str(value).strip()
        return text or None

    def read_metadata(self, path: Path) -> DatasetMetadata:
        """Read E57 file metadata."""
        import pye57

        e57 = pye57.E57(str(path))
        point_count = sum(
            e57.get_header(i).point_count for i in range(e57.scan_count)
        )

        crs = self._read_crs(e57)

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
        """Yield point data from E57 in chunks.

        `pye57` has no chunked read -- `read_scan_raw` returns a whole scan -- so `chunk_size` bounds
        what the caller receives, not what is read. Two things keep that to ONE scan rather than
        several: the raw dictionary is released as soon as its columns have been stacked (it holds a
        full-length array per dimension, so it is the larger of the two copies), and the stacked arrays
        are released at the end of each scan, before the next `read_scan_raw`. Without the second, a
        generator suspended in scan N+1 while the caller writes scan N would hold both.
        """
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

            del data

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

            xyz = intensity = rgb = None


class LasReader(PointCloudReader):
    """Reader for LAS/LAZ files."""

    def read_metadata(self, path: Path) -> DatasetMetadata:
        """Read LAS/LAZ file metadata."""
        import laspy

        with laspy.open(str(path)) as f:
            header = f.header
            point_count = header.point_count

            # A LAS file records its CRS as either VLR 2112 (OGC WKT, the LAS 1.4 form) or VLR 34735
            # (GeoTIFF GeoKeyDirectoryTag, the LAS 1.0-1.3 form). parse_crs() reads both; matching only
            # 2112 reports every GeoKey-tagged file as carrying no CRS.
            crs = None
            try:
                parsed = header.parse_crs()
                if parsed is not None:
                    crs = parsed.to_wkt()
            except Exception:
                # parse_crs raises on a malformed or partial GeoKey directory; a file carrying both
                # records is still readable through its WKT VLR.
                crs = None

            if crs is None:
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
        """Yield point data from PLY in chunks.

        `open3d` reads a whole cloud, so as with E57 `chunk_size` bounds what the caller receives rather
        than what is read. Colours are scaled per chunk instead of for the whole cloud: the scale
        promotes uint8 to float64, so doing it up front cost a second full-cloud array three times the
        size of the one it produced.
        """
        import open3d as o3d

        pcd = o3d.io.read_point_cloud(str(path))
        xyz = np.asarray(pcd.points, dtype=np.float64)

        colors = np.asarray(pcd.colors) if pcd.has_colors() else None
        normals = (
            np.asarray(pcd.normals, dtype=np.float32)
            if pcd.has_normals()
            else None
        )

        total_points = xyz.shape[0]
        for chunk_idx, start in enumerate(range(0, total_points, chunk_size)):
            end = min(start + chunk_size, total_points)
            yield PointChunk(
                xyz=xyz[start:end],
                rgb=(
                    (colors[start:end] * 255).astype(np.uint8)
                    if colors is not None
                    else None
                ),
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
