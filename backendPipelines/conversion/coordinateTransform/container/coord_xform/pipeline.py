"""Pipeline orchestration: reading, validation, transform, and writing."""

import json
from datetime import UTC, datetime
from pathlib import Path

import structlog

from coord_xform.config import OnMismatch, PipelineConfig
from coord_xform.models import (
    CameraExtrinsics,
    PipelineReport,
    PointChunk,
    ScanDataset,
    ValidationResult,
)
from coord_xform.readers import detect_format, get_reader
from coord_xform.scan_dataset import discover_scan_dataset
from coord_xform.transform import CoordinateTransformer
from coord_xform.validation import validate_inputs
from coord_xform.writers import get_writer

logger = structlog.get_logger()


def run_pipeline(config: PipelineConfig, inputs: list[Path]) -> PipelineReport:
    """Execute the full transformation pipeline."""
    log = logger.bind(
        source_crs=config.source.crs,
        target_crs=config.target.crs,
    )
    log.info("pipeline.start", input_count=len(inputs))

    validation_results = validate_inputs(config, inputs)
    _handle_validation(config, validation_results)

    transformer = CoordinateTransformer(config)
    target_wkt = transformer.target_crs.to_wkt()

    all_output_files: list[Path] = []
    total_points = 0
    max_residual = 0.0
    errors: list[str] = []

    cameras_transformed = 0

    for input_path in inputs:
        log.info("pipeline.processing", file=str(input_path))

        try:
            # Discover associated imagery if input is a directory or has siblings
            scan_dataset = discover_scan_dataset(input_path)
            pc_path = scan_dataset.point_cloud_path

            fmt = detect_format(pc_path)
            reader = get_reader(fmt)

            transformed_chunks: list[PointChunk] = []

            for chunk in reader.read_chunks(
                pc_path, config.transform.chunk_size
            ):
                result = transformer.transform_chunk(chunk)
                transformed_chunk = PointChunk(
                    xyz=result.xyz,
                    intensity=chunk.intensity,
                    rgb=chunk.rgb,
                    normals=chunk.normals,
                    classification=chunk.classification,
                    scan_index=chunk.scan_index,
                    chunk_index=chunk.chunk_index,
                    scan_metadata=chunk.scan_metadata,
                )
                transformed_chunks.append(transformed_chunk)
                total_points += chunk.count
                max_residual = max(max_residual, result.residual_error_mm)

            # Transform camera extrinsics if present
            transformed_camera = None
            if scan_dataset.camera is not None:
                transformed_camera = transformer.transform_camera(
                    scan_dataset.camera
                )
                cameras_transformed += 1
                log.info(
                    "camera.transformed",
                    original=scan_dataset.camera.position.tolist(),
                    transformed=transformed_camera.position.tolist(),
                    image=str(transformed_camera.image_path),
                )

            output_files = _write_outputs(
                config, pc_path, transformed_chunks, target_wkt
            )
            all_output_files.extend(output_files)

            # Copy associated images to output directory
            if scan_dataset.image_paths:
                _copy_images(config, scan_dataset.image_paths)

            # Write camera metadata if transformed
            if transformed_camera is not None:
                _write_camera_metadata(
                    config, pc_path, transformed_camera, scan_dataset
                )

        except Exception as exc:
            error_msg = f"{input_path}: {type(exc).__name__}: {exc}"
            errors.append(error_msg)
            log.error(
                "pipeline.file_failed",
                file=str(input_path),
                error=str(exc),
            )

    report = PipelineReport(
        input_files=inputs,
        source_crs=config.source.crs,
        target_crs=config.target.crs,
        scale_factor_applied=transformer.scale_factor,
        total_points_processed=total_points,
        output_files=all_output_files,
        residual_error_mm=max_residual,
        validation_results=validation_results,
        cameras_transformed=cameras_transformed,
        errors=errors,
    )

    _write_report(config, report)
    log.info(
        "pipeline.complete",
        total_points=total_points,
        output_files=len(all_output_files),
        residual_mm=max_residual,
    )

    return report


def _handle_validation(
    config: PipelineConfig, results: list[ValidationResult]
) -> None:
    """Handle validation results according to config."""
    failures = [r for r in results if not r.passed]

    if not failures:
        return

    match config.validation.on_mismatch:
        case OnMismatch.ERROR:
            messages = [f"  {r.file_path}: {r.message}" for r in failures]
            raise SystemExit(
                "CRS validation failed:\n" + "\n".join(messages)
            )
        case OnMismatch.WARN:
            for r in failures:
                logger.warning(
                    "validation.mismatch",
                    file=str(r.file_path),
                    message=r.message,
                )
        case OnMismatch.SKIP:
            pass


def _write_outputs(
    config: PipelineConfig,
    input_path: Path,
    chunks: list[PointChunk],
    crs_wkt: str,
) -> list[Path]:
    """Write transformed data to all configured output formats.

    Splits output by scan_index when multiple scans are present.
    """
    output_files: list[Path] = []
    output_dir = config.output.directory
    output_dir.mkdir(parents=True, exist_ok=True)

    base_stem = config.output.naming.format(
        input_stem=input_path.stem,
        target_crs=config.target.crs.replace(":", "_"),
    )

    # Group chunks by scan index
    scans: dict[int, list[PointChunk]] = {}
    for chunk in chunks:
        scans.setdefault(chunk.scan_index, []).append(chunk)

    multi_scan = len(scans) > 1

    for scan_idx in sorted(scans.keys()):
        scan_chunks = scans[scan_idx]
        stem = (
            f"{base_stem}_scan{scan_idx:03d}"
            if multi_scan
            else base_stem
        )

        for fmt in config.output.formats:
            writer = get_writer(fmt, compress=config.output.compress_laz)
            output_path = output_dir / f"{stem}.{fmt.value}"
            writer.write(output_path, scan_chunks, crs_wkt)
            output_files.append(output_path)
            logger.info(
                "output.written",
                format=fmt.value,
                path=str(output_path),
            )

        # Write scan metadata sidecar if available
        _write_scan_metadata(output_dir, stem, scan_chunks)

    return output_files


def _write_scan_metadata(
    output_dir: Path,
    stem: str,
    chunks: list[PointChunk],
) -> None:
    """Write scan metadata JSON sidecar if metadata is available."""
    meta = next(
        (c.scan_metadata for c in chunks if c.scan_metadata is not None),
        None,
    )
    if meta is None:
        return

    meta_path = output_dir / f"{stem}_scan_metadata.json"
    meta_data = {
        "scan_index": meta.scan_index,
        "name": meta.name,
        "guid": meta.guid,
        "timestamp": meta.timestamp,
        "sensor_model": meta.sensor_model,
        "sensor_serial": meta.sensor_serial,
        "temperature": meta.temperature,
        "humidity": meta.humidity,
    }

    with open(meta_path, "w") as f:
        json.dump(meta_data, f, indent=2)

    logger.info("scan_metadata.written", path=str(meta_path))


def _copy_images(config: PipelineConfig, image_paths: list[Path]) -> None:
    """Copy associated images to the output directory."""
    import shutil

    output_dir = config.output.directory / "images"
    output_dir.mkdir(parents=True, exist_ok=True)

    for img_path in image_paths:
        dest = output_dir / img_path.name
        if not dest.exists():
            shutil.copy2(img_path, dest)
            logger.info("image.copied", path=str(dest))


def _write_camera_metadata(
    config: PipelineConfig,
    pc_path: Path,
    camera: CameraExtrinsics,
    dataset: ScanDataset,
) -> None:
    """Write transformed camera metadata as a JSON sidecar."""
    output_dir = config.output.directory
    camera_path = output_dir / f"{pc_path.stem}_camera.json"

    camera_data = {
        "scan_index": camera.scan_index,
        "position": camera.position.tolist(),
        "orientation": (
            camera.orientation.tolist() if camera.orientation is not None else None
        ),
        "image_path": str(camera.image_path) if camera.image_path else None,
        "associated_images": [str(p.name) for p in dataset.image_paths],
    }

    with open(camera_path, "w") as f:
        json.dump(camera_data, f, indent=2)

    logger.info("camera_metadata.written", path=str(camera_path))


def _write_report(config: PipelineConfig, report: PipelineReport) -> None:
    """Write JSON transformation report."""
    output_dir = config.output.directory
    report_path = output_dir / "transform_report.json"

    report_data = {
        "timestamp": datetime.now(UTC).isoformat(),
        "pipeline": config.name,
        "version": config.version,
        "source_crs": report.source_crs,
        "target_crs": report.target_crs,
        "scale_factor_applied": report.scale_factor_applied,
        "total_points_processed": report.total_points_processed,
        "max_residual_error_mm": report.residual_error_mm,
        "input_files": [str(p) for p in report.input_files],
        "output_files": [str(p) for p in report.output_files],
        "validation": [
            {
                "file": str(r.file_path),
                "passed": r.passed,
                "message": r.message,
                "detected_crs": r.detected_crs,
                "expected_crs": r.expected_crs,
                "confidence": r.confidence.value,
            }
            for r in report.validation_results
        ],
        "cameras_transformed": report.cameras_transformed,
        "errors": report.errors,
    }

    with open(report_path, "w") as f:
        json.dump(report_data, f, indent=2)

    logger.info("report.written", path=str(report_path))
