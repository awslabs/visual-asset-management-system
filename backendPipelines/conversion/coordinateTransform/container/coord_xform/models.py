"""Shared data models for the pipeline."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import numpy as np
from numpy.typing import NDArray


class InputFormat(str, Enum):
    """Supported input file formats."""

    E57 = "e57"
    LAS = "las"
    LAZ = "laz"
    PLY = "ply"


@dataclass
class Bounds:
    """Axis-aligned bounding box."""

    min_x: float
    min_y: float
    min_z: float
    max_x: float
    max_y: float
    max_z: float

    def __str__(self) -> str:
        return (
            f"X[{self.min_x:.3f}, {self.max_x:.3f}] "
            f"Y[{self.min_y:.3f}, {self.max_y:.3f}] "
            f"Z[{self.min_z:.3f}, {self.max_z:.3f}]"
        )


@dataclass
class DatasetMetadata:
    """Metadata extracted from a point cloud file."""

    file_path: Path
    format: InputFormat
    crs: str | None
    point_count: int
    scan_count: int
    bounds: Bounds | None = None
    scale_factor: float | None = None
    extra: dict[str, str] = field(default_factory=dict)


@dataclass
class ScanMetadata:
    """Metadata associated with a single scan position."""

    scan_index: int = 0
    name: str | None = None
    guid: str | None = None
    timestamp: str | None = None
    sensor_model: str | None = None
    sensor_serial: str | None = None
    temperature: float | None = None
    humidity: float | None = None


@dataclass
class PointChunk:
    """A chunk of point cloud data for processing."""

    xyz: NDArray[np.float64]
    intensity: NDArray[np.float32] | None = None
    rgb: NDArray[np.uint8] | None = None
    normals: NDArray[np.float32] | None = None
    classification: NDArray[np.uint8] | None = None
    scan_index: int = 0
    chunk_index: int = 0
    scan_metadata: ScanMetadata | None = None

    @property
    def count(self) -> int:
        return self.xyz.shape[0]


@dataclass
class TransformResult:
    """Result of transforming a single chunk."""

    xyz: NDArray[np.float64]
    residual_error_mm: float
    scale_correction_applied: float


class CrsConfidence(str, Enum):
    """Confidence level for CRS detection."""

    HIGH = "high"
    LOW = "low"
    NONE = "none"


@dataclass
class ValidationResult:
    """Result of CRS validation for a single file."""

    file_path: Path
    passed: bool
    message: str
    detected_crs: str | None = None
    expected_crs: str | None = None
    confidence: CrsConfidence = CrsConfidence.NONE


@dataclass
class CameraExtrinsics:
    """Camera position and orientation associated with a scan."""

    position: NDArray[np.float64]  # [x, y, z]
    orientation: NDArray[np.float64] | None = None  # quaternion [w, x, y, z]
    image_path: Path | None = None
    scan_index: int = 0


@dataclass
class ScanDataset:
    """A complete scan dataset with point cloud and optional imagery."""

    point_cloud_path: Path
    image_paths: list[Path] = field(default_factory=list)
    camera: CameraExtrinsics | None = None
    metadata: DatasetMetadata | None = None


@dataclass
class PipelineReport:
    """Final report for a pipeline run."""

    input_files: list[Path]
    source_crs: str
    target_crs: str
    scale_factor_applied: float
    total_points_processed: int
    output_files: list[Path]
    residual_error_mm: float
    validation_results: list[ValidationResult] = field(default_factory=list)
    cameras_transformed: int = 0
    errors: list[str] = field(default_factory=list)
