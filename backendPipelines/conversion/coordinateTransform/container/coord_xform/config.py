"""Pipeline configuration models."""

from enum import Enum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class OnMismatch(str, Enum):
    """Action to take when CRS mismatch is detected."""

    ERROR = "error"
    WARN = "warn"
    SKIP = "skip"


class OutputFormat(str, Enum):
    """Supported output formats."""

    E57 = "e57"
    LAS = "las"
    LAZ = "laz"
    PLY = "ply"


class SourceConfig(BaseModel):
    """Source dataset CRS configuration."""

    crs: str = Field(description="EPSG code, WKT, or PROJ string for source CRS")
    scale_factor: float = Field(
        default=1.0, description="Source grid scale factor"
    )


class TargetConfig(BaseModel):
    """Target CRS configuration."""

    crs: str = Field(description="EPSG code, named custom grid, or PROJ string")
    scale_factor: float = Field(
        default=1.0, description="Target grid scale factor"
    )


class TransformConfig(BaseModel):
    """Transformation parameters."""

    apply_scale_correction: bool = Field(
        default=True,
        description="Whether to apply scale factor correction",
    )
    combined_scale_factor: float | None = Field(
        default=None,
        description="Override: apply a single combined scale factor directly",
    )
    chunk_size: int = Field(
        default=1_000_000, description="Points per processing chunk"
    )
    parallel_scans: bool = Field(
        default=True,
        description="Process scan positions in parallel",
    )


class ValidationConfig(BaseModel):
    """CRS validation settings."""

    enforce_source_crs: bool = Field(
        default=True,
        description="Block if detected CRS != configured source CRS",
    )
    on_mismatch: OnMismatch = Field(
        default=OnMismatch.ERROR,
        description="Action on CRS mismatch",
    )


class OutputConfig(BaseModel):
    """Output configuration."""

    formats: list[OutputFormat] = Field(
        default=[OutputFormat.E57, OutputFormat.LAZ, OutputFormat.PLY]
    )
    directory: Path = Field(default=Path("./output"))
    naming: str = Field(
        default="{input_stem}_{target_crs}",
        description="Output filename template",
    )
    compress_laz: bool = Field(default=True)


class CustomGrid(BaseModel):
    """Custom coordinate grid definition."""

    name: str = Field(description="Grid identifier (e.g., 'local+sizewell')")
    definition: str = Field(description="PROJ string defining the grid")


class PipelineConfig(BaseModel):
    """Top-level pipeline configuration."""

    name: str = Field(default="coordinate-transform")
    version: str = Field(default="1.0")
    source: SourceConfig
    target: TargetConfig
    transform: TransformConfig = Field(default_factory=TransformConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    custom_grids: list[CustomGrid] = Field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: Path) -> "PipelineConfig":
        """Load configuration from a YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f)
        pipeline_data = data.get("pipeline", {})
        config_data = {k: v for k, v in data.items() if k != "pipeline"}
        config_data.update(pipeline_data)
        return cls(**config_data)
