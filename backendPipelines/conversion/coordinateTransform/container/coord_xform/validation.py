"""CRS validation logic."""

import re
from pathlib import Path

import pyproj

from coord_xform.config import PipelineConfig
from coord_xform.models import CrsConfidence, ValidationResult
from coord_xform.readers import detect_format, get_reader


def validate_inputs(
    config: PipelineConfig, inputs: list[Path]
) -> list[ValidationResult]:
    """Validate CRS of all input files against expected configuration."""
    results: list[ValidationResult] = []

    for input_path in inputs:
        result = _validate_single(config, input_path)
        results.append(result)

    return results


def _validate_single(
    config: PipelineConfig, path: Path
) -> ValidationResult:
    """Validate a single input file's CRS."""
    try:
        fmt = detect_format(path)
        reader = get_reader(fmt)
        metadata = reader.read_metadata(path)
    except Exception as e:
        return ValidationResult(
            file_path=path,
            passed=False,
            message=f"Failed to read file: {e}",
        )

    if metadata.crs is None:
        if config.validation.enforce_source_crs:
            return ValidationResult(
                file_path=path,
                passed=False,
                message=(
                    "No CRS detected in file metadata;"
                    " source CRS enforcement is enabled"
                ),
                expected_crs=config.source.crs,
                confidence=CrsConfidence.NONE,
            )
        return ValidationResult(
            file_path=path,
            passed=True,
            message="No CRS detected; using configured source CRS",
            expected_crs=config.source.crs,
            confidence=CrsConfidence.LOW,
        )

    try:
        detected = _parse_crs(metadata.crs)
        expected = _resolve_expected_crs(config)

        if detected.equals(expected):
            return ValidationResult(
                file_path=path,
                passed=True,
                message=f"CRS matches expected: {config.source.crs}",
                detected_crs=metadata.crs,
                expected_crs=config.source.crs,
                confidence=CrsConfidence.HIGH,
            )

        return ValidationResult(
            file_path=path,
            passed=False,
            message=(
                f"CRS mismatch: detected '{detected.name}' "
                f"but expected '{expected.name}'"
            ),
            detected_crs=metadata.crs,
            expected_crs=config.source.crs,
            confidence=CrsConfidence.HIGH,
        )
    except Exception as e:
        return ValidationResult(
            file_path=path,
            passed=False,
            message=f"Failed to parse CRS: {e}",
            detected_crs=metadata.crs,
            expected_crs=config.source.crs,
            confidence=CrsConfidence.LOW,
        )


def _parse_crs(crs_string: str) -> pyproj.CRS:
    """Parse a CRS string, falling back to EPSG extraction from WKT."""
    try:
        return pyproj.CRS.from_user_input(crs_string)
    except Exception:
        pass

    epsg_match = re.search(r'ID\["EPSG"\s*,\s*(\d+)\]', crs_string)
    if epsg_match:
        return pyproj.CRS.from_epsg(int(epsg_match.group(1)))

    raise ValueError(f"Cannot parse CRS: {crs_string[:100]}")


def _resolve_expected_crs(config: PipelineConfig) -> pyproj.CRS:
    """Resolve the expected source CRS from config."""
    crs_spec = config.source.crs

    for grid in config.custom_grids:
        if grid.name == crs_spec:
            return pyproj.CRS.from_proj4(grid.definition)

    if crs_spec.startswith("EPSG:"):
        return pyproj.CRS.from_epsg(int(crs_spec.split(":")[1]))

    if crs_spec.startswith("+proj"):
        return pyproj.CRS.from_proj4(crs_spec)

    return pyproj.CRS.from_wkt(crs_spec)
