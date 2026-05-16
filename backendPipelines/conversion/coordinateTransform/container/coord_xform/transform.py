"""Coordinate transformation engine."""

import numpy as np
import pyproj
from numpy.typing import NDArray

from coord_xform.config import PipelineConfig
from coord_xform.models import CameraExtrinsics, PointChunk, TransformResult


class CoordinateTransformer:
    """Handles CRS reprojection and scale factor correction."""

    def __init__(self, config: PipelineConfig) -> None:
        self._config = config
        self._source_crs = self._resolve_crs(config.source.crs)
        self._target_crs = self._resolve_crs(config.target.crs)
        self._transformer = pyproj.Transformer.from_crs(
            self._source_crs,
            self._target_crs,
            always_xy=True,
        )
        self._scale_factor = self._compute_scale_factor()

    def _resolve_crs(self, crs_spec: str) -> pyproj.CRS:
        """Resolve a CRS specification to a pyproj CRS object."""
        for grid in self._config.custom_grids:
            if grid.name == crs_spec:
                return pyproj.CRS.from_proj4(grid.definition)

        if crs_spec.startswith("EPSG:"):
            return pyproj.CRS.from_epsg(int(crs_spec.split(":")[1]))

        if crs_spec.startswith("+proj"):
            return pyproj.CRS.from_proj4(crs_spec)

        return pyproj.CRS.from_wkt(crs_spec)

    def _compute_scale_factor(self) -> float:
        """Compute the effective scale factor to apply."""
        if self._config.transform.combined_scale_factor is not None:
            return self._config.transform.combined_scale_factor

        if not self._config.transform.apply_scale_correction:
            return 1.0

        source_sf = self._config.source.scale_factor
        target_sf = self._config.target.scale_factor
        return target_sf / source_sf

    def transform_chunk(self, chunk: PointChunk) -> TransformResult:
        """Transform a chunk of points from source to target CRS."""
        x, y, z = chunk.xyz[:, 0], chunk.xyz[:, 1], chunk.xyz[:, 2]

        tx, ty, tz = self._transformer.transform(x, y, z)

        transformed_xyz = np.column_stack([tx, ty, tz])

        if self._scale_factor != 1.0:
            transformed_xyz[:, 0] *= self._scale_factor
            transformed_xyz[:, 1] *= self._scale_factor

        residual = self._estimate_residual(chunk.xyz, transformed_xyz)

        return TransformResult(
            xyz=transformed_xyz,
            residual_error_mm=residual,
            scale_correction_applied=self._scale_factor,
        )

    def _estimate_residual(
        self,
        original: NDArray[np.float64],
        transformed: NDArray[np.float64],
    ) -> float:
        """Estimate residual error from the transformation in mm.

        For same-unit transforms (projected -> projected), compares
        inter-point distance ratios against the expected scale factor.
        For cross-unit transforms (projected -> geographic), this metric
        is not meaningful and returns -1.0 to indicate it was skipped.
        """
        if self._is_cross_unit_transform():
            return -1.0

        if original.shape[0] < 2:
            return 0.0

        orig_dists = np.linalg.norm(
            np.diff(original[:100], axis=0), axis=1
        )
        trans_dists = np.linalg.norm(
            np.diff(transformed[:100], axis=0), axis=1
        )

        mask = orig_dists > 0
        if not np.any(mask):
            return 0.0

        ratios = trans_dists[mask] / orig_dists[mask]
        expected_ratio = self._scale_factor
        deviation = np.abs(ratios - expected_ratio)
        mean_dev = float(np.mean(deviation))

        avg_dist_m = float(np.mean(orig_dists[mask]))
        residual_mm = mean_dev * avg_dist_m * 1000.0

        return residual_mm

    def _is_cross_unit_transform(self) -> bool:
        """Check if source and target CRS use different linear units."""
        source_axis = self._source_crs.axis_info
        target_axis = self._target_crs.axis_info
        if source_axis and target_axis:
            return source_axis[0].unit_name != target_axis[0].unit_name
        return False

    def transform_camera(self, camera: CameraExtrinsics) -> CameraExtrinsics:
        """Transform camera extrinsics from source to target CRS."""
        pos = camera.position.reshape(1, 3)
        x, y, z = pos[0, 0], pos[0, 1], pos[0, 2]

        tx, ty, tz = self._transformer.transform(x, y, z)

        transformed_pos = np.array([tx, ty, tz], dtype=np.float64)

        if self._scale_factor != 1.0:
            transformed_pos[0] *= self._scale_factor
            transformed_pos[1] *= self._scale_factor

        return CameraExtrinsics(
            position=transformed_pos,
            orientation=camera.orientation,
            image_path=camera.image_path,
            scan_index=camera.scan_index,
        )

    @property
    def source_crs(self) -> pyproj.CRS:
        return self._source_crs

    @property
    def target_crs(self) -> pyproj.CRS:
        return self._target_crs

    @property
    def scale_factor(self) -> float:
        return self._scale_factor
