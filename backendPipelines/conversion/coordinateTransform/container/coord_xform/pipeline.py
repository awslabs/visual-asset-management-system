"""Pipeline orchestration: reading, validation, transform, and writing."""

import json
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import structlog

from coord_xform.config import OnMismatch, OutputFormat, PipelineConfig
from coord_xform.models import (
    CameraExtrinsics,
    PipelineReport,
    PointChunk,
    ScanDataset,
    ScanMetadata,
    ValidationResult,
)
from coord_xform.readers import detect_format, get_reader
from coord_xform.scan_dataset import discover_scan_dataset
from coord_xform.spill import ChunkSpill
from coord_xform.transform import CoordinateTransformer
from coord_xform.validation import validate_inputs
from coord_xform.writers import get_writer

logger = structlog.get_logger()

# A CRS specification arrives as an EPSG code, a WKT string or a proj4 string and is caller-supplied,
# so only these characters of it survive into an output file name.
_CRS_TOKEN_DISALLOWED = re.compile(r"[^A-Za-z0-9_-]+")
_CRS_TOKEN_MAX_LENGTH = 48


class CrsValidationError(Exception):
    """An input's detected CRS contradicts the configured source CRS."""


def _check_output_compression_agrees(config: PipelineConfig) -> None:
    """Refuse a config whose ``compress_laz`` contradicts its output formats.

    LAZ is the compressed LAS format, so ``compress_laz=False`` alongside ``OutputFormat.LAZ`` asks
    for a compressed file and for it not to be compressed. Checked here rather than on
    ``OutputConfig``, because this is the single entry both routes into the container pass through —
    the VAMS pipeline via ``coord_transform_pipeline.core`` and ``coord-xform transform --config``
    via ``PipelineConfig.from_yaml`` — and because a pydantic validator would have to be written for
    one major version: the image pins pydantic 2 and the repository's test interpreter pins 1.10.13.

    Only that combination is refused. ``compress_laz`` defaults to True, so rejecting True alongside
    a laz-free format list would fail every ordinary LAS run that never mentioned it.
    """
    if config.output.compress_laz:
        return
    if OutputFormat.LAZ in config.output.formats:
        raise ValueError(
            "compress_laz is false but output formats request laz. LAZ is the compressed LAS "
            "format, so the two settings contradict: request las for uncompressed output, or "
            "leave compress_laz at its default."
        )


def run_pipeline(config: PipelineConfig, inputs: list[Path]) -> PipelineReport:
    """Execute the full transformation pipeline."""
    _check_output_compression_agrees(config)
    log = logger.bind(
        source_crs=config.source.crs,
        target_crs=config.target.crs,
    )
    log.info("pipeline.start", input_count=len(inputs))

    validation_results = validate_inputs(config, inputs)
    _handle_validation(config, validation_results)

    transformer = CoordinateTransformer(config)
    target_wkt = transformer.target_crs.to_wkt()

    config.output.directory.mkdir(parents=True, exist_ok=True)

    # Transformed points are spilled here, not to the output directory: the container uploads
    # everything it finds under the output directory, so a spill file left there would be published as
    # though it were a converted cloud. This sits on the same ephemeral volume as the container's work
    # directory, which is what the pipeline's Batch job definition sizes.
    spill_dir = Path(tempfile.mkdtemp(prefix="coord_xform_spill_"))

    all_output_files: list[Path] = []
    total_points = 0
    max_residual = 0.0
    errors: list[str] = []

    cameras_transformed = 0

    try:
        for input_path in inputs:
            log.info("pipeline.processing", file=str(input_path))

            try:
                # Discover associated imagery if input is a directory or has siblings
                scan_dataset = discover_scan_dataset(input_path)
                pc_path = scan_dataset.point_cloud_path

                fmt = detect_format(pc_path)
                reader = get_reader(fmt)

                base_stem = _output_base_stem(config, pc_path)

                # A scan is written as soon as its last chunk has been read, so only one scan's points
                # are held at a time -- and each of those chunks goes straight to a spill file, so the
                # points held in memory are one chunk's worth rather than one scan's. The first scan is
                # held back until a second one arrives, because the _scanNNN suffix applies only to a
                # file carrying more than one.
                held: ChunkSpill | None = None
                written_scans: set[int] = set()

                for chunk in reader.read_chunks(
                    pc_path, config.transform.chunk_size
                ):
                    result = transformer.transform_chunk(chunk)
                    total_points += chunk.count
                    max_residual = max(max_residual, result.residual_error_mm)

                    if held is not None and chunk.scan_index != held.scan_index:
                        if chunk.scan_index in written_scans:
                            raise ValueError(
                                f"scan {chunk.scan_index} of {pc_path.name} was already written; "
                                "a scan's chunks must be read consecutively"
                            )
                        written_scans.add(held.scan_index)
                        all_output_files.extend(
                            _write_scan_outputs(
                                config,
                                base_stem,
                                held,
                                target_wkt,
                                multi_scan=True,
                            )
                        )
                        held.discard()
                        held = None

                    if held is None:
                        held = ChunkSpill(spill_dir, chunk.scan_index)
                    held.append(
                        PointChunk(
                            xyz=result.xyz,
                            intensity=chunk.intensity,
                            rgb=chunk.rgb,
                            normals=chunk.normals,
                            classification=chunk.classification,
                            scan_index=chunk.scan_index,
                            chunk_index=chunk.chunk_index,
                            scan_metadata=chunk.scan_metadata,
                        )
                    )

                if held is not None:
                    all_output_files.extend(
                        _write_scan_outputs(
                            config,
                            base_stem,
                            held,
                            target_wkt,
                            multi_scan=bool(written_scans),
                        )
                    )
                    held.discard()
                    held = None

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
    finally:
        # A spill file is a full copy of the transformed cloud, so leaving one behind on a failure
        # halves what the next input has to work with on the same task.
        shutil.rmtree(spill_dir, ignore_errors=True)

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
            raise CrsValidationError(
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


def _crs_filename_token(crs: str) -> str:
    """Reduce a CRS specification to a token usable in a file name."""
    token = _CRS_TOKEN_DISALLOWED.sub("_", crs).strip("_-")
    if len(token) > _CRS_TOKEN_MAX_LENGTH:
        token = token[:_CRS_TOKEN_MAX_LENGTH].rstrip("_-")
    return token or "crs"


def _output_base_stem(config: PipelineConfig, input_path: Path) -> str:
    """Build the output file stem, rejecting anything that is not a single file name."""
    stem = config.output.naming.format(
        input_stem=input_path.stem,
        target_crs=_crs_filename_token(config.target.crs),
    )

    if stem in ("", ".", "..") or stem != Path(stem).name:
        raise ValueError(
            f"Output naming produced {stem!r}, which is not a single file name"
        )

    return stem


def _write_scan_outputs(
    config: PipelineConfig,
    base_stem: str,
    spill: ChunkSpill,
    crs_wkt: str,
    multi_scan: bool,
) -> list[Path]:
    """Write one scan's transformed data to all configured output formats.

    The spill is closed here rather than by the caller, because a writer reads it back and reading is
    only defined once writing has finished. It is read once per format, which is why `chunks()` reopens
    the file instead of consuming a single iterator.
    """
    spill.close()

    output_files: list[Path] = []
    output_dir = config.output.directory

    stem = (
        f"{base_stem}_scan{spill.scan_index:03d}" if multi_scan else base_stem
    )

    for fmt in config.output.formats:
        writer = get_writer(fmt)
        output_path = output_dir / f"{stem}.{fmt.value}"
        writer.write(output_path, spill, crs_wkt)
        output_files.append(output_path)
        logger.info(
            "output.written",
            format=fmt.value,
            path=str(output_path),
        )

    # Write scan metadata sidecar if available
    _write_scan_metadata(output_dir, stem, spill.scan_metadata)

    return output_files


def _write_scan_metadata(
    output_dir: Path,
    stem: str,
    meta: ScanMetadata | None,
) -> None:
    """Write scan metadata JSON sidecar if metadata is available."""
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
