# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Core pipeline runner for Coordinate Transform container."""

import json
import os
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

        # Run the coord_xform pipeline
        logger.info(
            f"Running transform: {pipeline_config.source.crs} "
            f"-> {pipeline_config.target.crs}"
        )
        report = run_pipeline(pipeline_config, [Path(local_input_path)])

        if report.errors:
            logger.warning(f"Pipeline completed with errors: {report.errors}")

        # Upload output files to S3
        if not local_test:
            _upload_outputs(
                output_dir, stage_output.bucketName, stage_output.objectDir
            )

            # Upload metadata (report JSON) if metadata output configured
            if stage_output_meta.bucketName and stage_output_meta.objectDir:
                _upload_metadata(
                    output_dir,
                    stage_output_meta.bucketName,
                    stage_output_meta.objectDir,
                )

        stage.status = PipelineStatus.COMPLETE
        logger.info(
            f"Transform complete. Points: {report.total_points_processed}, "
            f"Files: {len(report.output_files)}"
        )

    except Exception as e:
        logger.exception(e)
        stage.status = PipelineStatus.FAILED
        stage.errorMessage = str(e)

    return stage


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


def _upload_outputs(
    output_dir: str, bucket: str, output_prefix: str
) -> None:
    """Upload all output files from local directory to S3."""
    for root, _dirs, files in os.walk(output_dir):
        for filename in files:
            local_path = os.path.join(root, filename)
            relative_path = os.path.relpath(local_path, output_dir)
            object_key = os.path.join(
                output_prefix, relative_path
            ).replace("\\", "/")

            # Skip the report JSON from the main output path
            if filename == "transform_report.json":
                continue

            s3.upload(bucket, object_key, local_path)
            logger.info(f"Uploaded: s3://{bucket}/{object_key}")


def _upload_metadata(
    output_dir: str, bucket: str, metadata_prefix: str
) -> None:
    """Upload metadata files (report, scan metadata) to S3."""
    for root, _dirs, files in os.walk(output_dir):
        for filename in files:
            if filename.endswith(".json"):
                local_path = os.path.join(root, filename)
                object_key = os.path.join(
                    metadata_prefix, filename
                ).replace("\\", "/")
                s3.upload(bucket, object_key, local_path)
                logger.info(f"Uploaded metadata: s3://{bucket}/{object_key}")
