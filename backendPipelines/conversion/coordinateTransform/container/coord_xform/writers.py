"""Point cloud output writers.

A writer receives a closed `ChunkSpill` rather than a list of chunks, so no writer holds the whole
transformed cloud on behalf of the pipeline. What each format then does with it differs, and the
difference is a property of the format's own library rather than of this module:

* **LAS/LAZ streams.** laspy writes the header first and then appends point records, so the output is
  produced one chunk at a time and peak memory is one chunk. This is what `header.offsets` and
  `header.scales` being derivable from the spill's accumulated extent buys -- both are fixed before the
  first point is written.
* **E57 and PLY materialise one full copy.** `pye57.write_scan_raw` takes whole per-dimension arrays and
  `open3d` takes a whole `Vector3dVector`, so neither can be fed incrementally. They are filled from the
  spill into a single pre-allocated array, which is one copy rather than the retained chunk list plus a
  `vstack` of it. On an E57 INPUT the reader also holds the scan it is part-way through, so a
  multi-scan E57-to-E57 run peaks at that scan plus the array being filled.
"""

import tempfile
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np

from coord_xform.config import OutputFormat
from coord_xform.spill import ChunkSpill

# The largest value a LAS coordinate record holds. Coordinates are stored as int32 offsets from
# header.offsets, which sits at the per-axis minimum, so only the positive range is reachable.
_LAS_COORD_MAX = 2**31 - 1


class PointCloudWriter(ABC):
    """Base class for point cloud file writers."""

    @abstractmethod
    def write(
        self,
        path: Path,
        spill: ChunkSpill,
        crs_wkt: str,
    ) -> None:
        """Write transformed point data to file."""


class _CoordinateMetadataRoot:
    """The E57Root, with a CRS substituted for the empty `coordinateMetadata` placeholder.

    ASTM E2807 records the coordinate reference system on the E57Root as `coordinateMetadata`, and
    `pye57.E57.write_default_header` always creates that element as an empty string. libe57 refuses to
    replace an existing child element -- a later `set` raises `E57Exception ... (ErrorSetTwice)` -- so the
    value has to be supplied while the header is being written. Every other element is forwarded
    untouched, so the header keeps whatever pye57 puts in it.
    """

    def __init__(self, node, image_file, crs_wkt: str) -> None:
        self._node = node
        self._image_file = image_file
        self._crs_wkt = crs_wkt

    def set(self, name, node):
        if name == "coordinateMetadata":
            from pye57 import libe57

            node = libe57.StringNode(self._image_file, self._crs_wkt)
        return self._node.set(name, node)

    def __getattr__(self, name):
        return getattr(self._node, name)

    def __getitem__(self, key):
        return self._node[key]


def _open_e57_for_writing(path: str, crs_wkt: str):
    """Open an E57 for writing whose root records `crs_wkt`, or the plain writer when it is blank.

    A blank value keeps pye57's empty placeholder, because `E57Reader._read_crs` reads blank as "not
    recorded" rather than as a CRS whose name is empty.

    The subclass is built here rather than at module scope because `pye57` is a container-only
    dependency imported at call time.
    """
    import pye57

    if not crs_wkt or not crs_wkt.strip():
        return pye57.E57(path, mode="w")

    class _CrsE57(pye57.E57):
        def __init__(self) -> None:
            self._root_override = None
            super().__init__(path, mode="w")

        @property
        def root(self):
            if self._root_override is not None:
                return self._root_override
            return super().root

        def write_default_header(self) -> None:
            self._root_override = _CoordinateMetadataRoot(
                self.image_file.root(), self.image_file, crs_wkt
            )
            try:
                super().write_default_header()
            finally:
                self._root_override = None

    return _CrsE57()


def _materialise(
    spill: ChunkSpill, fields: tuple[str, ...]
) -> dict[str, np.ndarray]:
    """Fill one pre-allocated array per requested field from the spill.

    Pre-allocated and filled in place rather than concatenated, because a `vstack` of the read-back
    chunks would hold both the chunks and the result. Only for the formats whose libraries cannot be
    fed incrementally; the LAS/LAZ writer never calls this.

    `fields` names the OPTIONAL fields wanted alongside `xyz`; a field no chunk carried is omitted from
    the result rather than returned as zeros, so a caller can tell "absent" from "all zero".
    """
    count = spill.point_count
    out: dict[str, np.ndarray] = {
        "xyz": np.empty((count, 3), dtype=np.float64)
    }
    widths = {"intensity": 1, "rgb": 3, "normals": 3, "classification": 1}
    dtypes = {
        "intensity": np.float32,
        "rgb": np.uint8,
        "normals": np.float32,
        "classification": np.uint8,
    }
    for name in fields:
        if not spill.has_field(name):
            continue
        shape = (count,) if widths[name] == 1 else (count, widths[name])
        out[name] = np.empty(shape, dtype=dtypes[name])

    at = 0
    for chunk in spill.chunks():
        end = at + chunk.count
        out["xyz"][at:end] = chunk.xyz
        for name in fields:
            if name not in out:
                continue
            value = getattr(chunk, name)
            if value is None:
                raise ValueError(
                    f"chunk {chunk.chunk_index} of scan {chunk.scan_index} carries no {name} "
                    "while an earlier chunk did; the spilled scan is inconsistent"
                )
            out[name][at:end] = value
        at = end

    return out


class E57Writer(PointCloudWriter):
    """Writer for E57 format."""

    def write(
        self,
        path: Path,
        spill: ChunkSpill,
        crs_wkt: str,
    ) -> None:
        """Write point data to E57 file atomically."""
        path.parent.mkdir(parents=True, exist_ok=True)

        # Write to temp file first, then rename for atomicity
        tmp_fd, tmp_path = tempfile.mkstemp(
            suffix=".e57", dir=path.parent
        )
        import os
        os.close(tmp_fd)

        try:
            e57 = _open_e57_for_writing(tmp_path, crs_wkt)

            # One spill holds one scan -- the pipeline writes a scan as soon as its last chunk has been
            # read, and the `_scanNNN` suffix gives each its own file -- so this writes one scan.
            arrays = _materialise(spill, ("intensity", "rgb"))
            xyz = arrays["xyz"]

            data = {
                "cartesianX": xyz[:, 0],
                "cartesianY": xyz[:, 1],
                "cartesianZ": xyz[:, 2],
            }

            if "intensity" in arrays:
                data["intensity"] = arrays["intensity"]

            if "rgb" in arrays:
                rgb = arrays["rgb"]
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
    """Writer for LAS/LAZ format.

    `compress` selects the output extension, and the extension is what determines the encoding: laspy
    derives compression from the extension of a path destination (`.laz` compressed, `.las`
    uncompressed) and ignores its own `do_compress` argument in that case. LAS and LAZ are therefore
    not independently compressible -- the format is the compression setting.
    """

    def __init__(self, compress: bool = True) -> None:
        self._compress = compress

    def _compute_scales(
        self, min_xyz: np.ndarray, max_xyz: np.ndarray, crs_wkt: str
    ) -> np.ndarray:
        """Compute LAS scales from the CRS units, coarsened per axis to fit the data's extent.

        Takes the per-axis extent rather than the point array, because the header has to be written
        before the first point is appended. The extent is accumulated by the spill as chunks arrive and
        is elementwise equal to `np.min`/`np.max` over the whole array, so the scale this returns is
        unchanged from the one computed over a materialised cloud.
        """
        import pyproj

        crs = pyproj.CRS.from_wkt(crs_wkt)
        axis_info = crs.axis_info

        if axis_info and axis_info[0].unit_name == "degree":
            # For angular units: 1e-9 degrees ~ 0.0001m at equator
            preferred = np.array([1e-9, 1e-9, 0.0001])
        else:
            # For projected CRS (metres): 0.0001m = 0.1mm precision
            preferred = np.array([0.0001, 0.0001, 0.0001])

        # An axis spans at most _LAS_COORD_MAX * scale, so a scale finer than its extent requires
        # cannot represent the far end of the data. laspy rejects such a value rather than wrapping it,
        # which fails the write outright, so each axis takes the coarser of the preferred scale and the
        # one its own extent needs. Per axis, so a cloud that is wide in X keeps its Z precision.
        required = (
            np.asarray(max_xyz, dtype=np.float64)
            - np.asarray(min_xyz, dtype=np.float64)
        ) / _LAS_COORD_MAX

        return np.maximum(preferred, required)

    def write(
        self,
        path: Path,
        spill: ChunkSpill,
        crs_wkt: str,
    ) -> None:
        """Write point data to LAS/LAZ file atomically, one spilled chunk at a time."""
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
            point_format = laspy.PointFormat(2)
            header = laspy.LasHeader(point_format=point_format)
            header.offsets = spill.min_xyz
            header.scales = self._compute_scales(
                spill.min_xyz, spill.max_xyz, crs_wkt
            )

            import pyproj

            crs_obj = pyproj.CRS.from_wkt(crs_wkt)
            header.add_crs(crs_obj)

            # `laspy.open` writes the header on construction and then appends records, so the offsets
            # and scales above are fixed for the whole file -- which is why they come from the spill's
            # accumulated extent rather than from the points. It is handed a PATH, not an open stream:
            # laspy derives compression from a path's extension and defaults an open stream to
            # UNCOMPRESSED whatever the file is called.
            with laspy.open(tmp_path, mode="w", header=header) as writer:
                for chunk in spill.chunks():
                    record = laspy.ScaleAwarePointRecord.zeros(
                        chunk.count, header=header
                    )
                    record.x = chunk.xyz[:, 0]
                    record.y = chunk.xyz[:, 1]
                    record.z = chunk.xyz[:, 2]
                    if chunk.intensity is not None:
                        record.intensity = chunk.intensity.astype(np.uint16)
                    if chunk.rgb is not None:
                        record.red = chunk.rgb[:, 0].astype(np.uint16) * 256
                        record.green = chunk.rgb[:, 1].astype(np.uint16) * 256
                        record.blue = chunk.rgb[:, 2].astype(np.uint16) * 256
                    writer.write_points(record)

            Path(tmp_path).replace(final_path)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise


class PlyWriter(PointCloudWriter):
    """Writer for PLY format using Open3D."""

    def write(
        self,
        path: Path,
        spill: ChunkSpill,
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
            arrays = _materialise(spill, ("rgb", "normals"))
            pcd = o3d.geometry.PointCloud()
            pcd.points = o3d.utility.Vector3dVector(arrays["xyz"])

            if "rgb" in arrays:
                pcd.colors = o3d.utility.Vector3dVector(
                    arrays["rgb"].astype(np.float64) / 255.0
                )

            if "normals" in arrays:
                pcd.normals = o3d.utility.Vector3dVector(arrays["normals"])

            o3d.io.write_point_cloud(tmp_path, pcd)
            Path(tmp_path).replace(final_path)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise


def get_writer(fmt: OutputFormat) -> PointCloudWriter:
    """Get the appropriate writer for an output format.

    Compression is a property of the format rather than a separate setting: LAS is uncompressed, LAZ is
    compressed, and both remain selectable through the output format list.
    """
    match fmt:
        case OutputFormat.E57:
            return E57Writer()
        case OutputFormat.LAS:
            return LasWriter(compress=False)
        case OutputFormat.LAZ:
            return LasWriter(compress=True)
        case OutputFormat.PLY:
            return PlyWriter()
