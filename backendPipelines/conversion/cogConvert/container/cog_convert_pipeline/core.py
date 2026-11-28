# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Core runner of the TIF to COG conversion container.

One input file in, one `<name>.cog.tif` out beside it, in the same asset. The original is never written.
Every failure route reports on the internal task token: a stage that fails returns FAILED to `run`, and
`run` reports it; anything `run` itself raises is reported by `__main__`.
"""

import json
import os
import shutil
import tempfile

from . import convert
from .tiff_header import HeaderRejected, needs_conversion, read_header
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

# Reserved results object name read by the workflow's process-output step (backendPipelines/CLAUDE.md).
EXECUTION_STATUS_FILENAME = "execution.status.json"
SKIP_NOTE_FILENAME = "cog-convert-skipped.json"
# The task failure name of a file the pipeline declines to convert (over a limit, crossing the antimeridian, no room).
REFUSED_ERROR_NAME = "CogConvertRefused"


def run(params: dict, input_asset: dict = None) -> PipelineExecutionParams:
    """Run the conversion and report the outcome on the internal task token. ``input_asset`` is where the
    input sits in its asset, from its manifest entry (``{"relativePath", "assetRootS3Key",
    "assetRootBucket"}``); None when the launching Lambda named none, and the output subfolder is then
    derived from the input key."""
    definition = PipelineDefinition(**params)
    s3.register_bucket_regions(definition.bucketRegions)
    logger.info(f"Pipeline Definition: {definition.jobName}")

    if definition.currentStage is None:
        current_stage = PipelineStage(**definition.stages.pop(0))
        definition.currentStage = current_stage
    else:
        # A resumed definition arrives deserialized from JSON, where the stage is a plain dict.
        current_stage = (PipelineStage(**definition.currentStage)
                         if isinstance(definition.currentStage, dict) else definition.currentStage)

    if current_stage.type != PipelineType.COG_CONVERT:
        error_msg = f"Stage type {current_stage.type} not supported. Expected COG_CONVERT."
        logger.error(error_msg)
        current_stage.status = PipelineStatus.FAILED
        current_stage.errorMessage = error_msg
        result_stage = current_stage
    else:
        result_stage = _run_convert_stage(
            current_stage, definition.inputParameters, definition.localTest == "True", definition.assetId,
            (input_asset or {}).get("relativePath") or "")

    if definition.completedStages is None:
        definition.completedStages = []
    definition.completedStages.append(result_stage)
    definition.currentStage = None

    output = _build_output(definition, current_stage, result_stage.status)

    sfn.send_task_heartbeat(definition.externalSfnTaskToken)

    if result_stage.status is PipelineStatus.FAILED:
        sfn.send_task_failure(
            result_stage.errorMessage or "Unknown error", result_stage.errorName or sfn.DEFAULT_ERROR_NAME)
    else:
        try:
            sfn.send_task_success(output)
        except Exception as error:  # noqa: BLE001 - a rejected success must still end the task
            logger.exception(error)
            sfn.send_task_failure(f"The result could not be reported: {error}")
            output.status = PipelineStatus.FAILED
    return output


def _build_output(definition, stage, status) -> PipelineExecutionParams:
    return PipelineExecutionParams(
        jobName=definition.jobName,
        currentStageType=stage.type,
        definition=[definition.to_json()],
        inputMetadata=definition.inputMetadata,
        inputParameters=definition.inputParameters,
        externalSfnTaskToken=definition.externalSfnTaskToken,
        status=status,
    )


def _results_target(stage: PipelineStage):
    out = StageOutput(**stage.outputResults) if isinstance(stage.outputResults, dict) else stage.outputResults
    if out and out.bucketName and out.objectDir:
        return out
    return None


def _write_result(stage: PipelineStage, file_name: str, body: dict) -> bool:
    target = _results_target(stage)
    if target is None:
        logger.info(f"No results folder for this run; {file_name} not written")
        return False
    key = convert.join_key(target.objectDir, file_name)
    return s3.put_text(target.bucketName, key, json.dumps(body)) is not None


def _run_convert_stage(stage: PipelineStage, input_parameters, local_test: bool,
                       asset_id: str = "", relative_path: str = "") -> PipelineStage:
    stage.status = PipelineStatus.RUNNING
    logger.info("Starting COG conversion stage")
    work_dir = None
    try:
        stage_input = StageInput(**stage.inputFile)
        stage_output = StageOutput(**stage.outputFiles)
        stage_meta = StageOutput(**stage.outputMetadata)
        params = convert.parse_params(input_parameters)

        if local_test:
            logger.info("Local test mode - skipping S3 download")
            stage.status = PipelineStatus.COMPLETE
            return stage

        work_dir = tempfile.mkdtemp(prefix="cog_convert_")
        input_dir = os.path.join(work_dir, "input")
        output_dir = os.path.join(work_dir, "output")
        os.makedirs(input_dir)
        os.makedirs(output_dir)

        input_name = os.path.basename(stage_input.objectKey)
        subdir = convert.relative_subdir(stage_input.objectKey, asset_id, relative_path)
        local_input = os.path.join(input_dir, input_name)

        # The version converted is recorded on the COG; a run with no launch version reads the current object
        # and records the version HeadObject names for it.
        head = s3.head(stage_input.bucketName, stage_input.objectKey, stage_input.versionId)
        if head is None:
            raise RuntimeError(f"Cannot read s3://{stage_input.bucketName}/{stage_input.objectKey}")
        source_version = stage_input.versionId or head.get("VersionId", "") or ""
        if source_version == "null":
            source_version = ""
        input_bytes = int(head.get("ContentLength", 0))
        convert.check_input_size(input_bytes)
        convert.check_download_disk(work_dir, input_bytes)

        version = {"version_id": source_version} if source_version else {}
        if s3.download(stage_input.bucketName, stage_input.objectKey, local_input, **version) is None:
            raise RuntimeError(f"Failed to download s3://{stage_input.bucketName}/{stage_input.objectKey}")

        try:
            header = read_header(local_input)
        except HeaderRejected as error:
            raise RuntimeError(f"The input is not a TIFF this pipeline reads: {error}") from error

        proceed, reason = needs_conversion(header)
        if not proceed:
            logger.info(f"Nothing to convert: {reason}")
            _write_result(stage, SKIP_NOTE_FILENAME, {"outcome": "skipped", "reason": reason,
                                                       "file": convert.asset_relative_path(subdir, input_name)})
            stage.status = PipelineStatus.COMPLETE
            return stage
        logger.info(f"Converting: {reason}")

        input_size = os.path.getsize(local_input)
        convert.check_limits(header, input_size)

        # GDAL describes the file once: the longitude extent (an antimeridian crossing is refused) and the
        # nodata values (a source with nodata is converted through a mask band).
        memory = convert.task_memory_bytes()
        info = convert.inspect(local_input, memory)
        convert.check_antimeridian(info)
        source = convert.prepare_source(local_input, work_dir, info, memory)
        pixels = convert.estimate_output_pixels(
            header.width, header.height, convert.warped_size(source, work_dir, memory))
        output_bytes = convert.estimate_output_bytes(header, pixels)
        logger.info(f"Estimated output: up to {pixels} pixels, {output_bytes} bytes uncompressed")
        convert.check_disk(work_dir, output_bytes, input_size)

        output_name = convert.derived_output_name(input_name)
        local_output = os.path.join(output_dir, output_name)
        convert.convert(source, local_output, params)
        convert.validate_output(local_output)
        out_header = read_header(local_output)

        output_key = convert.join_key(stage_output.objectDir, subdir, output_name)
        if s3.upload(stage_output.bucketName, output_key, local_output) is None:
            raise RuntimeError(f"Failed to upload the COG to s3://{stage_output.bucketName}/{output_key}")
        logger.info(f"Uploaded: s3://{stage_output.bucketName}/{output_key}")

        # The COG is now in the output. A failure from here on is reported through the results channel so the
        # file is still ingested; raising would route to SendTaskFailure and discard it.
        failure = _write_link_attributes(
            stage_meta, subdir, input_name, output_name, source_version, params, header, out_header)
        if failure:
            logger.error(failure)
            if _write_result(stage, EXECUTION_STATUS_FILENAME, {
                    "status": "FAILED", "error": "CogLinkAttributesNotWritten", "cause": failure[:1024]}):
                stage.status = PipelineStatus.COMPLETE
                return stage
            raise RuntimeError(failure)

        stage.status = PipelineStatus.COMPLETE
    except (Exception, SystemExit) as error:
        logger.exception(error)
        stage.status = PipelineStatus.FAILED
        stage.errorMessage = str(error)
        stage.errorName = (
            REFUSED_ERROR_NAME if isinstance(error, convert.ConversionRefused) else sfn.DEFAULT_ERROR_NAME)
    finally:
        if work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)
    return stage


def _write_link_attributes(stage_meta, subdir, input_name, output_name, source_version, params, header,
                           out_header):
    """Write the `cog_link` attributes on the COG and on its source. Returns a failure message, or None."""
    if not (stage_meta.bucketName and stage_meta.objectDir):
        logger.warning("No metadata output is configured for this run; the cog_link attributes are not written")
        return None
    output_crs = out_header.crs
    documents = {
        f"{output_name}.attribute.json": convert.cog_attributes(
            convert.asset_relative_path(subdir, input_name), source_version, params["compression"],
            convert.gdal_version(), header.crs, output_crs),
        f"{input_name}.attribute.json": convert.source_attributes(
            convert.asset_relative_path(subdir, output_name)),
    }
    failed = []
    for name, body in documents.items():
        key = convert.join_key(stage_meta.objectDir, subdir, name)
        if s3.put_text(stage_meta.bucketName, key, body) is None:
            failed.append(key)
    if failed:
        return ("The COG was written but its link attributes were not: " + ", ".join(failed))
    return None
