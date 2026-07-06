"""Point cloud output writers."""

import tempfile
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np

from coord_xform.config import OutputFormat
from coord_xform.models import PointChunk


class PointCloudWriter(ABC):
    """Base class for point cloud file writers."""

    @abstractmethod
    def write(
        self,
        path: Path,
        chunks: list[PointChunk],
        crs_wkt: str,
    ) -> None:
        """Write transformed point data to file."""


class E57Writer(PointCloudWriter):
    """Writer for E57 format."""

    def write(
        self,
        path: Path,
        chunks: list[PointChunk],
        crs_wkt: str,
    ) -> None:
        """Write point data to E57 file atomically."""
        import pye57

        path.parent.mkdir(parents=True, exist_ok=True)

        # Write to temp file first, then rename for atomicity
        tmp_fd, tmp_path = tempfile.mkstemp(
            suffix=".e57", dir=path.parent
        )
        import os
        os.close(tmp_fd)

        try:
            e57 = pye57.E57(tmp_path, mode="w")

            scans: dict[int, list[PointChunk]] = {}
            for chunk in chunks:
                scans.setdefault(chunk.scan_index, []).append(chunk)

            for scan_idx in sorted(scans.keys()):
                scan_chunks = scans[scan_idx]
                xyz = np.vstack([c.xyz for c in scan_chunks])

                data = {
                    "cartesianX": xyz[:, 0],
                    "cartesianY": xyz[:, 1],
                    "cartesianZ": xyz[:, 2],
                }

                intensities = [
                    c.intensity
                    for c in scan_chunks
                    if c.intensity is not None
                ]
                if intensities:
                    data["intensity"] = np.concatenate(intensities)

                rgbs = [
                    c.rgb for c in scan_chunks if c.rgb is not None
                ]
                if rgbs:
                    rgb = np.vstack(rgbs)
                    data["colorRed"] = rgb[:, 0]
                    data["colorGreen"] = rgb[:, 1]
                    data["colorBlue"] = rgb[:, 2]

                e57.write_scan_raw(data)

            e57.close()
            Path(tmp_path).replace(path)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise


class LasWriter(PointCloudWriter):
    """Writer for LAS/LAZ format."""

    def __init__(self, compress: bool = True) -> None:
        self._compress = compress

    def _compute_scales(
        self, xyz: np.ndarray, crs_wkt: str
    ) -> np.ndarray:
        """Compute appropriate LAS scales based on CRS units."""
        import pyproj

        crs = pyproj.CRS.from_wkt(crs_wkt)
        axis_info = crs.axis_info

        if axis_info and axis_info[0].unit_name == "degree":
            # For angular units: 1e-9 degrees ~ 0.0001m at equator
            return np.array([1e-9, 1e-9, 0.0001])

        # For projected CRS (metres): 0.0001m = 0.1mm precision
        return np.array([0.0001, 0.0001, 0.0001])

    def write(
        self,
        path: Path,
        chunks: list[PointChunk],
        crs_wkt: str,
    ) -> None:
        """Write point data to LAS/LAZ file atomically."""
        import laspy

        path.parent.mkdir(parents=True, exist_ok=True)

        if self._compress:
            final_path = path.with_suffix(".laz")
        else:
            final_path = path.with_suffix(".las")

        tmp_fd, tmp_path = tempfile.mkstemp(
            suffix=final_path.suffix, dir=path.parent
        )
        import os
        os.close(tmp_fd)

        try:
            xyz = np.vstack([c.xyz for c in chunks])

            point_format = laspy.PointFormat(2)
            header = laspy.LasHeader(point_format=point_format)
            header.offsets = np.min(xyz, axis=0)
            header.scales = self._compute_scales(xyz, crs_wkt)

            import pyproj

            crs_obj = pyproj.CRS.from_wkt(crs_wkt)
            header.add_crs(crs_obj)

            las = laspy.LasData(header)
            las.x = xyz[:, 0]
            las.y = xyz[:, 1]
            las.z = xyz[:, 2]

            intensities = [
                c.intensity for c in chunks if c.intensity is not None
            ]
            if intensities:
                las.intensity = np.concatenate(intensities).astype(np.uint16)

            rgbs = [c.rgb for c in chunks if c.rgb is not None]
            if rgbs:
                rgb = np.vstack(rgbs)
                las.red = rgb[:, 0].astype(np.uint16) * 256
                las.green = rgb[:, 1].astype(np.uint16) * 256
                las.blue = rgb[:, 2].astype(np.uint16) * 256

            las.write(tmp_path)
            Path(tmp_path).replace(final_path)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise


class PlyWriter(PointCloudWriter):
    """Writer for PLY format using Open3D."""

    def write(
        self,
        path: Path,
        chunks: list[PointChunk],
        crs_wkt: str,
    ) -> None:
        """Write point data to PLY file atomically."""
        import open3d as o3d

        path.parent.mkdir(parents=True, exist_ok=True)
        final_path = path.with_suffix(".ply")

        tmp_fd, tmp_path = tempfile.mkstemp(
            suffix=".ply", dir=path.parent
        )
        import os
        os.close(tmp_fd)

        try:
            xyz = np.vstack([c.xyz for c in chunks])
            pcd = o3d.geometry.PointCloud()
            pcd.points = o3d.utility.Vector3dVector(xyz)

            rgbs = [c.rgb for c in chunks if c.rgb is not None]
            if rgbs:
                rgb = np.vstack(rgbs).astype(np.float64) / 255.0
                pcd.colors = o3d.utility.Vector3dVector(rgb)

            normals_list = [
                c.normals for c in chunks if c.normals is not None
            ]
            if normals_list:
                normals = np.vstack(normals_list)
                pcd.normals = o3d.utility.Vector3dVector(normals)

            o3d.io.write_point_cloud(tmp_path, pcd)
            Path(tmp_path).replace(final_path)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise


def get_writer(fmt: OutputFormat, compress: bool = True) -> PointCloudWriter:
    """Get the appropriate writer for an output format."""
    match fmt:
        case OutputFormat.E57:
            return E57Writer()
        case OutputFormat.LAS:
            return LasWriter(compress=False)
        case OutputFormat.LAZ:
            return LasWriter(compress=True)
        case OutputFormat.PLY:
            return PlyWriter()
