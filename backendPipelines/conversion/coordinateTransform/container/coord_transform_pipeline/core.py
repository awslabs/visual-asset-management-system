# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Core pipeline runner for Coordinate Transform container."""

import json
import math
import os
import shutil
import struct
import tempfile
from pathlib import Path

from .utils.aws import s3, sfn
from .utils.logging import log
from .utils.pipeline.objects import (
    PipelineDefinition,
    PipelineExecutionParams,
    PipelineStage,
    PipelineStatus,
    PipelineType,
    StageInput,
    StageOutput,
)

logger = log.get_logger()

# The transform spills every transformed point to disk before writing an output, so the volume has to
# hold the downloaded input, one spill copy, and every requested output at once. Expressed as multiples
# of the DOWNLOADED size, because that is the only figure available before the file is opened.
#
# The spill carries the point payload uncompressed (xyz float64, plus intensity/colour where present),
# so against a compressed LAZ input it is several times the file on disk. 4x covers a LAZ compression
# ratio around 5:1 with margin; an uncompressed LAS or E57 input needs closer to 1x, so the same figure
# is generous there rather than wrong. Each requested output format is budgeted at 3x for the same
# reason -- an uncompressed LAS or PLY written from a LAZ input is larger than its input.
_SPILL_SIZE_FACTOR = 4.0
_OUTPUT_SIZE_FACTOR_PER_FORMAT = 3.0


def _check_transform_disk_budget(
    work_dir: str, input_path: str, output_format_count: int
) -> None:
    """Refuse a run whose spill plus outputs cannot fit, before the transform is paid for.

    Without this the volume fills part-way through the transform pass -- on exactly the large inputs
    this budget is about -- and the run has already spent its reprojection time. The message names disk
    explicitly, because the `OSError` errno 28 that would otherwise surface reaches the execution record
    as a bare "No space left on device" with no figures to size the volume from.
    """
    try:
        input_bytes = os.path.getsize(input_path)
        free = shutil.disk_usage(work_dir).free
    except OSError as e:
        # A pre-flight estimate must never be the thing that fails a run that would otherwise work. If
        # the staged input cannot be sized the download did not produce a file, and the download's own
        # error is the one worth reporting; the spill write still raises errno 28 if the volume fills,
        # which `_run_transform_stage` catches and reports. Skipping only loses the early warning.
        logger.info(f"Disk budget check skipped: {e}")
        return

    required = int(
        input_bytes
        * (
            1.0
            + _SPILL_SIZE_FACTOR
            + _OUTPUT_SIZE_FACTOR_PER_FORMAT * max(output_format_count, 1)
        )
    )
    logger.info(
        f"Disk budget: input={input_bytes} bytes, estimated need={required} bytes "
        f"({output_format_count} output format(s)), free={free} bytes on {work_dir}"
    )
    if free < required:
        raise RuntimeError(
            f"Not enough ephemeral disk for this transform: the input is "
            f"{input_bytes / 2**30:.1f} GiB and the run needs about "
            f"{required / 2**30:.1f} GiB for the spill plus {output_format_count} output "
            f"format(s), but only {free / 2**30:.1f} GiB is free. Request fewer output formats, "
            f"or raise the pipeline's Batch ephemeral storage "
            f"(ephemeralStorageGiB in coordinateTransform-construct.ts)."
        )


def _log_peak_memory(chunk_size: int, total_points: int) -> None:
    """Log this process's peak resident memory alongside the two figures it should be compared against.

    The transform runs in-process, so `RUSAGE_SELF` is the whole cost, and this is the only figure that
    shows whether `chunkSize` bounds memory rather than merely bounding what the reader yields: a peak
    that tracks the point count instead of the chunk size means the cloud is resident. `ru_maxrss` is in
    kilobytes on Linux.

    `resource` is POSIX-only and imported here rather than at module scope: this module is imported by
    the repository's test suites, which run on developer workstations as well as in the Linux container.
    """
    try:
        import resource  # noqa: PLC0415 -- POSIX-only; see the docstring
    except ImportError:
        return
    logger.info(
        "Peak resident memory: "
        f"{resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20:.2f} GiB "
        f"(chunkSize={chunk_size}, points={total_points})"
    )


def run(params: dict) -> PipelineExecutionParams:
    """Run the coordinate transform pipeline."""
    definition = PipelineDefinition(**params)
    logger.info(f"Pipeline Definition: {definition.jobName}")

    if definition.currentStage is None:
        current_stage = PipelineStage(**definition.stages.pop(0))
        definition.currentStage = current_stage

    if current_stage.type != PipelineType.COORD_TRANSFORM:
        error_msg = (
            f"Stage type {current_stage.type} not supported. "
            "Expected COORD_TRANSFORM."
        )
        logger.error(error_msg)
        output = _build_output(definition, current_stage, PipelineStatus.FAILED)
        return output

    result_stage = _run_transform_stage(
        current_stage,
        definition.inputMetadata,
        definition.inputParameters,
        definition.localTest == "True",
        definition.assetId,
    )

    if definition.completedStages is None:
        definition.completedStages = []
    definition.completedStages.append(result_stage)
    definition.currentStage = None

    output = _build_output(definition, current_stage, result_stage.status)

    sfn.send_task_heartbeat(definition.externalSfnTaskToken)

    if result_stage.status is PipelineStatus.FAILED:
        sfn.send_task_failure(result_stage.errorMessage or "Unknown error")
    else:
        sfn.send_task_success(output)

    return output


def _build_output(
    definition: PipelineDefinition,
    stage: PipelineStage,
    status: PipelineStatus,
) -> PipelineExecutionParams:
    return PipelineExecutionParams(
        jobName=definition.jobName,
        currentStageType=stage.type,
        definition=[definition.to_json()],
        inputMetadata=definition.inputMetadata,
        inputParameters=definition.inputParameters,
        externalSfnTaskToken=definition.externalSfnTaskToken,
        status=status,
    )


def _run_transform_stage(
    stage: PipelineStage,
    input_metadata: str,
    input_parameters: str,
    local_test: bool,
    asset_id: str = "",
) -> PipelineStage:
    """Execute the coordinate transformation stage."""
    from coord_xform.config import (
        OnMismatch,
        OutputConfig,
        OutputFormat,
        PipelineConfig,
        SourceConfig,
        TargetConfig,
        TransformConfig,
        ValidationConfig,
    )
    from coord_xform.pipeline import run_pipeline

    stage.status = PipelineStatus.RUNNING
    logger.info("Starting coordinate transform stage")

    try:
        stage_input = StageInput(**stage.inputFile)
        stage_output = StageOutput(**stage.outputFiles)
        stage_output_meta = StageOutput(**stage.outputMetadata)

        # Parse transform config from inputParameters
        transform_params = {}
        if input_parameters:
            transform_params = (
                json.loads(input_parameters)
                if isinstance(input_parameters, str)
                else input_parameters
            )

        if not transform_params.get("sourceCrs") or not transform_params.get(
            "targetCrs"
        ):
            raise ValueError(
                "inputParameters must include 'sourceCrs' and 'targetCrs'"
            )

        # Create working directories
        work_dir = tempfile.mkdtemp(prefix="coord_xform_")
        input_dir = os.path.join(work_dir, "input")
        output_dir = os.path.join(work_dir, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        # Download input file from S3
        input_filename = os.path.basename(stage_input.objectKey)
        local_input_path = os.path.join(input_dir, input_filename)

        if not local_test:
            downloaded = s3.download(
                stage_input.bucketName,
                stage_input.objectKey,
                local_input_path,
            )
            if downloaded is None:
                raise RuntimeError(
                    f"Failed to download s3://{stage_input.bucketName}"
                    f"/{stage_input.objectKey}"
                )
        else:
            logger.info("Local test mode - skipping S3 download")
            stage.status = PipelineStatus.COMPLETE
            return stage

        # Build PipelineConfig from transform parameters
        output_formats = _parse_output_formats(
            transform_params.get("outputFormats", ["laz"])
        )

        pipeline_config = PipelineConfig(
            name="vams-coordinate-transform",
            version="1.0",
            source=SourceConfig(
                crs=transform_params["sourceCrs"],
                scale_factor=transform_params.get("sourceScaleFactor", 1.0),
            ),
            target=TargetConfig(
                crs=transform_params["targetCrs"],
                scale_factor=transform_params.get("targetScaleFactor", 1.0),
            ),
            transform=TransformConfig(
                apply_scale_correction=transform_params.get(
                    "applyScaleCorrection", True
                ),
                combined_scale_factor=transform_params.get(
                    "combinedScaleFactor"
                ),
                chunk_size=transform_params.get("chunkSize", 1_000_000),
            ),
            validation=ValidationConfig(
                enforce_source_crs=transform_params.get(
                    "enforceSourceCrs", True
                ),
                on_mismatch=OnMismatch(
                    transform_params.get("onMismatch", "warn")
                ),
            ),
            output=OutputConfig(
                formats=output_formats,
                directory=Path(output_dir),
                compress_laz=transform_params.get("compressLaz", True),
            ),
        )

        # Refuse a run the volume cannot hold before the reprojection is paid for.
        _check_transform_disk_budget(
            work_dir, local_input_path, len(output_formats)
        )

        # Run the coord_xform pipeline
        logger.info(
            f"Running transform: {pipeline_config.source.crs} "
            f"-> {pipeline_config.target.crs}"
        )
        report = run_pipeline(pipeline_config, [Path(local_input_path)])

        _log_peak_memory(
            pipeline_config.transform.chunk_size, report.total_points_processed
        )

        # A reported error fails the stage: a run that could not transform its input must not be
        # recorded as a successful conversion.
        if report.errors:
            raise RuntimeError(
                f"Transform reported {len(report.errors)} error(s): {report.errors}"
            )

        # An empty output list is the other way a run that transformed nothing reaches the end of this
        # function. coord_xform records an error for a file it could not read, but a reader that yields
        # no chunks writes no file and reports nothing, so the report is clean and the output list empty.
        if not report.output_files:
            raise RuntimeError(
                "Transform produced no output files, so there is nothing to publish"
            )

        # Validate what was written, not just that writing returned. A reprojection can produce
        # coordinates outside double-precision range and still write a file whose header claims every
        # input point, so each output is read back before it is published.
        _validate_transform_outputs(report, output_dir)

        # Upload output files to S3
        if not local_test:
            relative_subdir = _relative_subdir_from_object_key(
                stage_input.objectKey, asset_id
            )
            metadata_configured = bool(
                stage_output_meta.bucketName and stage_output_meta.objectDir
            )
            _upload_outputs(
                output_dir,
                stage_output.bucketName,
                stage_output.objectDir,
                relative_subdir,
                metadata_configured,
            )

            # Upload metadata (report JSON, sidecars) if metadata output configured
            if metadata_configured:
                _upload_metadata(
                    output_dir,
                    stage_output_meta.bucketName,
                    stage_output_meta.objectDir,
                    relative_subdir,
                )

        stage.status = PipelineStatus.COMPLETE
        logger.info(
            f"Transform complete. Points: {report.total_points_processed}, "
            f"Files: {len(report.output_files)}"
        )

    # SystemExit is named alongside Exception because coord_xform raises it, not a subclass of
    # Exception, when on_mismatch is ERROR (coord_xform/pipeline.py:152). Uncaught it skips run()'s
    # reporting block entirely, so the internal task token is never reported: the container exits
    # non-zero but the WAIT_FOR_TASK_TOKEN task cannot see that, and the sub-state-machine waits its
    # full 4-hour taskTimeout before States.ALL routes it to pipelineEnd. Caught here, the same
    # validation failure becomes a FAILED stage and an immediate SendTaskFailure.
    except (Exception, SystemExit) as e:
        logger.exception(e)
        stage.status = PipelineStatus.FAILED
        stage.errorMessage = str(e)

    return stage


# An E57 file is stored as 1024-byte physical pages, each ending with a 4-byte checksum, so a value long
# enough to cross a page boundary has those bytes spliced into it. Reading the logical stream means
# dropping them.
_E57_PAGE_SIZE = 1024
_E57_PAGE_PAYLOAD = 1020
# The XML section holds one element per scan, so a many-scan file's is large while the root's own CRS sits
# among the first elements. Read a bounded prefix rather than the whole section.
_E57_XML_READ_MAX = 4 * 1024 * 1024


def _e57_recorded_crs(section: bytes) -> str | None:
    """The CRS an E57 records on its root, or None when it records none.

    `section` starts at a page boundary, so dropping each page's trailing checksum yields the logical
    bytes the element spans. A `coordinateMetadata` element that is self-closing or empty reads as absent,
    matching `E57Reader._read_crs`, whose blank value means "not recorded".
    """
    logical = b"".join(
        section[offset : offset + _E57_PAGE_PAYLOAD]
        for offset in range(0, len(section), _E57_PAGE_SIZE)
    )

    start = logical.find(b"<coordinateMetadata")
    if start < 0:
        return None
    tag_end = logical.find(b">", start)
    if tag_end < 0 or logical[tag_end - 1 : tag_end] == b"/":
        return None

    close = logical.find(b"</coordinateMetadata>", tag_end)
    if close < 0:
        return None

    body = logical[tag_end + 1 : close]
    if body.startswith(b"<![CDATA[") and body.endswith(b"]]>"):
        body = body[len(b"<![CDATA[") : -len(b"]]>")]

    return body.decode("utf-8", "replace").strip() or None


def _check_e57_output(path: Path, problems: list) -> None:
    """Append a problem unless `path` is an E57 whose root records a coordinate reference system."""
    try:
        with open(path, "rb") as handle:
            header = handle.read(48)
            if len(header) < 40 or header[:8] != b"ASTM-E57":
                problems.append(f"{path.name}: not an E57 file after writing")
                return

            # E57 file header: xmlPhysicalOffset at 24, xmlLogicalLength at 32, both little-endian u64.
            xml_offset, xml_length = struct.unpack_from("<2Q", header, 24)
            if not xml_length:
                problems.append(f"{path.name}: E57 XML section is empty after writing")
                return

            page_start = (xml_offset // _E57_PAGE_SIZE) * _E57_PAGE_SIZE
            span = min(xml_length, _E57_XML_READ_MAX)
            handle.seek(page_start)
            section = handle.read(
                (span // _E57_PAGE_PAYLOAD + 2) * _E57_PAGE_SIZE
            )
    except OSError as error:
        problems.append(f"{path.name}: could not be read back ({error})")
        return

    if not _e57_recorded_crs(section):
        problems.append(
            f"{path.name}: records no coordinate reference system on its E57Root, so the reprojected "
            f"coordinates carry no record of the CRS they are in"
        )


def _validate_transform_outputs(report, output_dir: str) -> None:
    """Reject an output whose own contents show the reprojection did not produce usable coordinates.

    Two properties are checked per written LAS/LAZ file, both read from the LAS header (which a LAZ file
    carries uncompressed at its start, so no decompression is needed):

    * the bounding box is FINITE. A reprojection that pushes coordinates outside double-precision range
      leaves min/max at DBL_MAX and +inf.
    * the bounding box is not inverted (min greater than max), which is the signature of a header whose
      bounds were never updated because no point was successfully written.

    The point count is deliberately NOT compared against the input: a transform may legitimately drop
    points that fall outside the target CRS's area of use, so a lower count is not by itself an error. The
    corrupt case is distinguishable without that comparison, because its bounds are not finite.

    A written E57 is checked for the one property its format carries and LAS does not encode the same way:
    the target CRS recorded on the E57Root as `coordinateMetadata`. An E57 whose root records no CRS is
    coordinates with no record of which system they are in, and it is what makes a second run over the
    file's own output fail source-CRS enforcement. The check reads the file back rather than trusting the
    write, because the value is set through libe57 and a dropped `set` would otherwise be silent.

    Raises RuntimeError, which the caller turns into a FAILED stage and a SendTaskFailure, so the
    execution is recorded as failed rather than as a successful conversion.
    """
    problems = []
    for path in sorted(Path(output_dir).rglob("*")):
        if path.suffix.lower() == ".e57" and path.is_file():
            _check_e57_output(path, problems)
            continue
        if path.suffix.lower() not in (".las", ".laz") or not path.is_file():
            continue
        try:
            with open(path, "rb") as handle:
                header = handle.read(512)
        except OSError as error:
            problems.append(f"{path.name}: could not be read back ({error})")
            continue
        if len(header) < 227 or header[:4] != b"LASF":
            problems.append(f"{path.name}: not a LAS/LAZ file after writing")
            continue

        # LAS 1.2+ header: max/min X, Y, Z as alternating doubles from offset 179.
        try:
            bounds = struct.unpack_from("<6d", header, 179)
        except struct.error:
            problems.append(f"{path.name}: LAS header too short to carry a bounding box")
            continue

        if any(not math.isfinite(v) for v in bounds):
            problems.append(
                f"{path.name}: bounding box is not finite {bounds}, so the reprojection produced "
                f"unusable coordinates"
            )
            continue
        for axis, (high, low) in zip("XYZ", zip(bounds[0::2], bounds[1::2])):
            if low > high:
                problems.append(f"{path.name}: {axis} bounds inverted (min {low} > max {high})")

    if problems:
        raise RuntimeError(
            "Transform wrote output that failed validation: " + "; ".join(problems)
        )


def _parse_output_formats(format_list: list[str]) -> list:
    """Convert string format names to OutputFormat enum values."""
    from coord_xform.config import OutputFormat

    mapping = {
        "e57": OutputFormat.E57,
        "las": OutputFormat.LAS,
        "laz": OutputFormat.LAZ,
        "ply": OutputFormat.PLY,
    }
    formats = []
    for fmt in format_list:
        if fmt.lower() in mapping:
            formats.append(mapping[fmt.lower()])
        else:
            logger.warning(f"Unknown output format: {fmt}, skipping")
    return formats or [OutputFormat.LAZ]


# The run report, and the sidecars coord_xform writes alongside the point clouds, describe a run and
# its outputs rather than being outputs themselves, so they belong under the asset metadata prefix.
# Sent to the asset files prefix they become versioned asset files of their own.
RUN_REPORT_FILENAME = "transform_report.json"
METADATA_SIDECAR_SUFFIXES = ("_scan_metadata.json", "_camera.json")


def _is_metadata_sidecar(filename: str) -> bool:
    return filename.endswith(METADATA_SIDECAR_SUFFIXES)


def _relative_subdir_from_object_key(object_key: str, asset_id: str) -> str:
    """The input file's subdirectory within the asset, sliced at the threaded assetId segment.

    `xd130a6d6/scans/room1/cloud.laz` with assetId `xd130a6d6` gives `scans/room1`; an input at the
    asset root gives `""`. The assetId is threaded through the pipeline definition rather than inferred
    from the key, so a key that does not contain it — a direct invoke carrying no asset — yields no
    subdirectory and the outputs land at the output prefix root, as they did before.
    """
    if not asset_id:
        return ""
    parts = object_key.split("/")
    if asset_id not in parts:
        logger.warning(
            f"assetId {asset_id} is not a segment of {object_key}; "
            "output will be written at the output prefix root"
        )
        return ""
    return "/".join(parts[parts.index(asset_id) + 1:-1])


def _join_key(*segments: str) -> str:
    """Join S3 key segments with single separators, dropping the empty ones."""
    parts = [segment.strip("/") for segment in segments if segment]
    return "/".join(part for part in parts if part)


def _upload_outputs(
    output_dir: str,
    bucket: str,
    output_prefix: str,
    relative_subdir: str = "",
    metadata_configured: bool = False,
) -> None:
    """Upload the transformed files to the asset files path.

    Each output keeps the input file's own subdirectory within the asset, so the write-back step places
    it beside its input. Without that, output from every subfolder collapses to the asset root and two
    inputs sharing a stem overwrite each other's result.

    A failed upload fails the stage rather than being logged and passed over: `s3.upload` returns None
    on a ClientError, so a discarded return turns an access or KMS denial into a completed conversion
    with nothing in the bucket.
    """
    failed = []
    for root, _dirs, files in os.walk(output_dir):
        for filename in files:
            if filename == RUN_REPORT_FILENAME:
                continue
            # With no metadata destination configured the sidecars stay here rather than being dropped.
            if metadata_configured and _is_metadata_sidecar(filename):
                continue

            local_path = os.path.join(root, filename)
            relative_path = os.path.relpath(local_path, output_dir).replace(
                "\\", "/"
            )
            object_key = _join_key(output_prefix, relative_subdir, relative_path)

            if s3.upload(bucket, object_key, local_path) is None:
                failed.append(object_key)
            else:
                logger.info(f"Uploaded: s3://{bucket}/{object_key}")

    if failed:
        raise RuntimeError(
            f"Failed to upload {len(failed)} output file(s) to s3://{bucket}: "
            + ", ".join(failed)
        )


def _upload_metadata(
    output_dir: str,
    bucket: str,
    metadata_prefix: str,
    relative_subdir: str = "",
) -> None:
    """Upload the run report and the metadata sidecars to the asset metadata path.

    Only those files: the metadata path's contents are interpreted by file name, so anything else a
    writer leaves in the output directory does not belong here.
    """
    failed = []
    for root, _dirs, files in os.walk(output_dir):
        for filename in files:
            if filename != RUN_REPORT_FILENAME and not _is_metadata_sidecar(
                filename
            ):
                continue

            local_path = os.path.join(root, filename)
            relative_path = os.path.relpath(local_path, output_dir).replace(
                "\\", "/"
            )
            object_key = _join_key(
                metadata_prefix, relative_subdir, relative_path
            )

            if s3.upload(bucket, object_key, local_path) is None:
                failed.append(object_key)
            else:
                logger.info(f"Uploaded metadata: s3://{bucket}/{object_key}")

    if failed:
        raise RuntimeError(
            f"Failed to upload {len(failed)} metadata file(s) to s3://{bucket}: "
            + ", ".join(failed)
        )
