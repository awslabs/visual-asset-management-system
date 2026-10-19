# Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Core pipeline orchestrator for Preview 3D Thumbnail generation.
Follows the pcPotreeViewer pipeline pattern: parse input, dispatch to handler, upload output.
"""

import os
import numpy as np
from pathlib import Path

from .utils.pipeline.objects import (
    PipelineDefinition,
    PipelineExecutionParams,
    PipelineStage,
    PipelineStatus,
    PipelineType,
    StageInput,
    StageOutput,
)
from .utils.pipeline import sfn
from .utils.logging import get_logger
from .utils import s3_utils as s3
from .utils import manifest_io
from .utils import image_utils
from .format_handlers import mesh_handler, pointcloud_handler, cad_handler, usd_handler, splat_handler
from .format_handlers import ply_header, xml_mesh_handler
from . import dependencies
from . import geometry_budget
from . import renderer
from . import resources
from .utils.timing import PhaseTimer

logger = get_logger()

# Thumbnail formats the pipeline emits. auto animates when the renderer produced several frames and
# writes a still otherwise; a named format applies to every input.
OUTPUT_TYPE_AUTO = "auto"
OUTPUT_TYPES = (OUTPUT_TYPE_AUTO, "gif", "jpg", "png")


def hello():
    logger.info("Preview 3D Thumbnail Pipeline - Generates preview images from 3D files")


def run(params: dict) -> PipelineExecutionParams:
    """
    Core runner for Preview 3D Thumbnail Pipeline.
    """
    # Convert input to data type
    definition = PipelineDefinition(**params)
    # Sign each asset bucket's requests for its own Region before any S3 call (see utils s3 helper).
    s3.register_bucket_regions(definition.bucketRegions)
    # Log the job name only: the definition carries externalSfnTaskToken, and this container's
    # plain-logging output is not redacted on its way to CloudWatch.
    logger.info(f"Pipeline Definition: {definition.jobName}")

    # Read the metadata + input configuration from S3
    input_metadata = manifest_io.fetch_metadata(definition.inputMetadataS3Location)
    input_configuration = manifest_io.fetch_input_configuration(definition.inputConfigurationS3Location)
    logger.info(f"Resolved input configuration keys: {list(input_configuration.keys())}")

    # Set pipeline current stage
    if definition.currentStage is None:
        current_stage = PipelineStage(**definition.stages.pop(0))
        definition.currentStage = current_stage
        logger.info(f"Pipeline Current Stage: {current_stage}")
    else:
        # A resumed definition already carries its stage. It arrives deserialized from JSON, where
        # the stage is a plain dict, so it is coerced back; an in-process definition already holds
        # the object. Without this branch current_stage is unbound and the reference below raises
        # UnboundLocalError -- and nothing above run() catches it, so the container ends without
        # reporting against the workflow's task token and the step waits out its full taskTimeout.
        current_stage = (
            PipelineStage(**definition.currentStage)
            if isinstance(definition.currentStage, dict)
            else definition.currentStage
        )

    # Verify pipeline type
    if current_stage.type != PipelineType.PREVIEW_3D_THUMBNAIL:
        logger.error(f"Pipeline Type {current_stage.type} not supported")
        output = PipelineExecutionParams(
            definition.jobName,
            current_stage.type,
            [definition.to_json()],
            definition.inputMetadataS3Location,
            definition.inputConfigurationS3Location,
            definition.externalSfnTaskToken,
            PipelineStatus.FAILED,
        )
        if definition.localTest == "False":
            sfn.send_task_failure(f"Pipeline Type {current_stage.type} not supported")
        return output

    # Run the preview generation pipeline
    # Wrap in try-except to convert unexpected exceptions into error responses.
    # The normal flow below handles the SFN callback — we avoid sending it here
    # to prevent double-send if the callback itself raises.
    try:
        resultStageCompleted = _run_preview_pipeline(
            current_stage,
            input_metadata,
            input_configuration,
            definition.localTest == "True",
            definition.assetId,
        )
    except Exception as e:
        logger.exception(f"Unexpected error in preview pipeline: {e}")
        resultStageCompleted = _error_response(
            current_stage, f"Unexpected pipeline error: {str(e)}"
        )
    logger.info(f"Pipeline Result: {resultStageCompleted}")

    if len(definition.stages) > 0 and definition.stages[0] is not None:
        next_stage_type = definition.stages[0]["type"]
    else:
        next_stage_type = None

    # Complete stage and reset current stage
    if definition.completedStages is None:
        definition.completedStages = []

    definition.completedStages.append(resultStageCompleted)
    definition.currentStage = None

    output = PipelineExecutionParams(
        definition.jobName,
        next_stage_type,
        [definition.to_json()],
        definition.inputMetadataS3Location,
        definition.inputConfigurationS3Location,
        definition.externalSfnTaskToken,
        resultStageCompleted.status,
    )

    # Send external sfn heartbeat (will fail silently on any problems)
    sfn.send_external_task_heartbeat(definition.externalSfnTaskToken)

    # Send SFN response on non localTest
    if definition.localTest == "False":
        if resultStageCompleted.status is PipelineStatus.FAILED:
            sfn.send_task_failure(resultStageCompleted.errorMessage)
        else:
            sfn.send_task_success(output)

    return output


def _run_preview_pipeline(
    stage: PipelineStage,
    inputMetadata: dict = None,
    inputParameters: dict = None,
    localTest: bool = False,
    assetId: str = "",
) -> PipelineStage:
    """
    Run the preview generation pipeline for a single stage, logging the run's compute resources,
    render budgets and per-phase timings.
    """
    budgets = resources.compute_budgets()
    tier = resources.compute_tier()
    if tier:
        logger.info(f"{resources.COMPUTE_TIER_ENV}={tier}")
    logger.info(f"Render budgets: {budgets.describe()}")
    timer = PhaseTimer()
    try:
        return _run_preview_stage(stage, inputMetadata, inputParameters, localTest, assetId,
                                  budgets, timer)
    finally:
        logger.info(timer.summary())


def _run_preview_stage(
    stage: PipelineStage,
    inputMetadata: dict,
    inputParameters: dict,
    localTest: bool,
    assetId: str,
    budgets: "resources.Budgets",
    timer: PhaseTimer,
) -> PipelineStage:
    """
    1. Download input file from S3 (a line-oriented point cloud is streamed instead)
    2. Detect format and load with appropriate handler, within the render budgets
    3. Render rotating preview frames
    4. Save as GIF (with size optimization / JPEG fallback)
    5. Upload to S3 output directory

    inputMetadata / inputParameters are already-parsed objects read from S3 by the caller.
    """
    # Input parameters are already a parsed object
    inputParametersObject = inputParameters if isinstance(inputParameters, dict) else {}

    # Create local working directories
    local_input_dir = _create_dir(["tmp", "input"])
    local_output_dir = _create_dir(["tmp", "output"])

    logger.info("Running Preview 3D Thumbnail Pipeline...")
    logger.info(f"Stage: {stage}")

    # Get pipeline stage input and output
    stage_input = StageInput(**stage.inputFile)
    stage_output = StageOutput(**stage.outputFiles)

    relative_subdir = _relative_subdir(stage_input, stage_output, assetId)
    logger.info(f"Input relative subdirectory: '{relative_subdir}'")

    # Parse overwriteExistingPreviewFiles parameter (default: True)
    overwrite_existing = True
    if isinstance(inputParametersObject, dict):
        overwrite_existing = inputParametersObject.get("overwriteExistingPreviewFiles", True)
        if not isinstance(overwrite_existing, bool):
            # A value supplied as text keeps an existing preview only when it reads as false;
            # anything else carries the default.
            overwrite_existing = str(overwrite_existing).strip().lower() not in ("false", "0", "no")
    logger.info(f"overwriteExistingPreviewFiles: {overwrite_existing}")

    # Parse outputType parameter (default: auto)
    output_type = _resolve_output_type(inputParametersObject)
    logger.info(f"outputType: {output_type}")

    # Check if a preview file already exists for this input file
    input_basename = os.path.basename(stage_input.objectKey) if stage_input.objectKey else ""
    if input_basename:
        existing_preview = _check_existing_preview(stage_input, input_basename, localTest)
        if existing_preview and not overwrite_existing:
            return _success_response(
                stage,
                f"A preview file already exists for '{input_basename}': {existing_preview}. "
                f"Skipping generation. Set inputParameters.overwriteExistingPreviewFiles to true "
                f"to regenerate.",
            )
        elif existing_preview and overwrite_existing:
            logger.info(
                f"Existing preview found ({existing_preview}), overwriting as requested."
            )

    # Detect format from the stage's declared extension, else the key.
    declared_ext = ("." + stage_input.fileExtension.lower().lstrip(".")) if stage_input.fileExtension else ""
    key_ext = os.path.splitext(stage_input.objectKey or "")[1].lower()

    # Download file from S3
    streamed = False
    if localTest:
        # localTest mode: use file from /data/input/ volume mount
        if stage_input.objectKey and stage_input.objectKey != "":
            # Specific file path provided via CLI argument
            local_filepath = stage_input.objectKey
        else:
            # Find first supported file in /data/input/
            local_filepath = _find_local_test_file("/data/input")
        logger.info(f"Using local test file: {local_filepath}")
        if local_filepath and os.path.isfile(local_filepath):
            logger.info(f"Input size: {os.path.getsize(local_filepath) / 1024 ** 3:.3f} GiB")
    else:
        # Check file size in S3 before downloading
        file_size = s3.get_object_size(stage_input.bucketName, stage_input.objectKey)
        if file_size is None:
            logger.warning(
                "Unable to determine file size via HEAD request. "
                "Proceeding with download; the free-disk check cannot run before it."
            )
        else:
            logger.info(f"Input size: {file_size / 1024 ** 3:.3f} GiB ({file_size:,} bytes)")

        # A line-oriented point cloud is read straight from S3 and sampled as it streams, so it needs
        # no local copy and is not limited by the work volume.
        streamed = pointcloud_handler.is_streamed(declared_ext or key_ext)
        if streamed:
            local_filepath = None
        else:
            if file_size is not None:
                try:
                    resources.ensure_disk_space(local_input_dir, file_size)
                except resources.InsufficientDiskError as e:
                    return _error_response(stage, str(e))

            logger.info(f"Downloading file from S3: {stage_input.bucketName}/{stage_input.objectKey}")
            # The input lands at its path relative to its asset's root, so the files it references are
            # fetched beside it at their own relative paths and resolve locally as they do in the asset.
            reference_root_key = dependencies.asset_root_key(
                stage_input.objectKey,
                assetId,
                stage_output.objectDir or "",
                same_bucket_output=bool(stage_output.bucketName)
                and stage_input.bucketName == stage_output.bucketName,
            )
            reference_local_root = os.path.join(local_input_dir, "asset")
            input_target = dependencies.local_path_for(
                reference_local_root,
                dependencies.relative_to_root(stage_input.objectKey, reference_root_key),
            ) or os.path.join(local_input_dir, os.path.basename(stage_input.objectKey))
            os.makedirs(os.path.dirname(input_target), exist_ok=True)
            with timer.phase("download"):
                local_filepath = s3.download(stage_input.bucketName, stage_input.objectKey, input_target)

    # Verify download
    if not streamed and (local_filepath is None or not os.path.isfile(local_filepath)):
        return _error_response(
            stage,
            "Unable to download file from S3 and/or no input file provided. "
            "Check bucket name, object key, and local input parameters.",
        )

    ext = declared_ext or (key_ext if streamed else os.path.splitext(local_filepath)[1].lower())

    logger.info(f"Detected file extension: {ext}")

    # In S3 mode, fetch the files the input references (glTF buffers/images, USD layers and textures,
    # OBJ materials and textures, FBX textures) from the input's own asset. A rejected or missing
    # reference is skipped; the input still renders.
    if not localTest and not streamed and dependencies.has_references(ext):
        try:
            with timer.phase("fetch"):
                dependencies.fetch_referenced_files(
                    stage_input.bucketName,
                    stage_input.objectKey,
                    local_filepath,
                    reference_local_root,
                    reference_root_key,
                    ext,
                )
        except Exception as e:
            logger.warning(f"Failed to fetch some referenced files: {e}")

    # Load with appropriate handler, then bring the geometry within the render budgets
    try:
        with timer.phase("load"):
            if streamed:
                pv_data = pointcloud_handler.load_stream(
                    s3.iter_object_bytes(stage_input.bucketName, stage_input.objectKey), ext,
                    max_points=budgets.points)
            else:
                pv_data = _load_file(local_filepath, ext,
                                     asset_root=None if localTest else reference_local_root,
                                     budgets=budgets)
            pv_data = geometry_budget.apply_budgets(pv_data, budgets)
    except MemoryError:
        logger.exception("Out of memory while loading the input")
        return _error_response(
            stage,
            f"Out of memory loading the 3D file ({ext}) within the task's "
            f"{budgets.memory_bytes / 1024 ** 3:.1f} GiB memory limit. "
            f"{resources.memory_advice(resources.compute_tier())}")
    except Exception as e:
        logger.exception(f"Failed to load file: {e}")
        return _error_response(stage, f"Failed to load 3D file ({ext}): {str(e)}")

    # Detect and normalize up-axis to Y-up for consistent rendering (splat scenes are oriented by
    # their handler)
    try:
        if not splat_handler.is_splat(pv_data):
            pv_data = _normalize_up_axis(pv_data, ext)
    except Exception as e:
        logger.warning(f"Up-axis normalization failed, rendering with original orientation: {e}")

    # Generate preview frames. A still output renders the front three-quarter view only; an animated
    # or automatic output renders the orbit, with that still as the fallback. The renderer frames a
    # mesh on its full bounds and a point cloud on its percentile bounds (stray points ignored).
    still_requested = output_type in ("jpg", "png")
    try:
        with timer.phase("render"):
            if still_requested:
                frames = [renderer.generate_static_frame(pv_data)]
            else:
                frames = renderer.generate_rotating_frames(pv_data)
    except Exception as e:
        logger.exception(f"Failed to render frames: {e}")
        if still_requested:
            return _error_response(stage, f"Failed to render preview: {str(e)}")
        # Fall back to static frame
        try:
            logger.info("Attempting static frame fallback...")
            with timer.phase("render"):
                static_img = renderer.generate_static_frame(pv_data)
            frames = [static_img]
        except Exception as e2:
            logger.exception(f"Static frame fallback also failed: {e2}")
            return _error_response(stage, f"Failed to render preview: {str(e)}")

    # Save and optimize output
    try:
        # Use actual file basename (from local_filepath for localTest, objectKey for S3; a streamed
        # input has no local copy)
        input_basename = os.path.basename(local_filepath or stage_input.objectKey)
        # auto animates whenever the renderer produced more than one frame; a named format applies
        # whatever the frame count. A still is the front three-quarter view: the rendered still, or
        # the orbit frame nearest that angle when only an orbit exists.
        animate = len(frames) > 1 if output_type == OUTPUT_TYPE_AUTO else output_type == "gif"
        still_extension = "png" if output_type == "png" else "jpg"
        extension = "gif" if animate else still_extension
        output_filename = f"{input_basename}.previewFile.{extension}"

        output_path = os.path.join(local_output_dir, output_filename)
        still_frame = frames[renderer.still_view_frame_index(len(frames))]

        with timer.phase("encode"):
            if animate:
                final_path = image_utils.ensure_under_size_limit(frames, output_path,
                                                                 still_frame=still_frame)
            else:
                if still_extension == "png":
                    image_utils.save_png(still_frame, output_path)
                else:
                    image_utils.save_jpeg(still_frame, output_path)
                final_path = output_path

        logger.info(f"Preview file generated: {final_path} "
                     f"({os.path.getsize(final_path) / 1024:.1f} KB)")
    except Exception as e:
        logger.exception(f"Failed to save preview image: {e}")
        return _error_response(stage, f"Failed to save preview image: {str(e)}")

    # Upload to S3 (or copy to local output in localTest mode)
    # Preserve the relative subdirectory from the input path in the output
    # so process-output can find files at the expected relative location.
    try:
        final_filename = os.path.basename(final_path)

        if localTest:
            # In localTest mode, copy output to the mounted /data/output directory
            import shutil
            local_output_base = os.path.join(stage_output.objectDir, relative_subdir) if relative_subdir else stage_output.objectDir
            if not os.path.isdir(local_output_base):
                os.makedirs(local_output_base, exist_ok=True)
            output_dest = os.path.join(local_output_base, final_filename)
            shutil.copy2(final_path, output_dest)
            logger.info(f"Local test output saved to: {output_dest}")
        else:
            # Build S3 key preserving relative subdirectory from input
            if relative_subdir:
                s3_object_key = stage_output.objectDir + relative_subdir + "/" + final_filename
            else:
                s3_object_key = stage_output.objectDir + final_filename
            logger.info(f"Uploading preview to S3: {stage_output.bucketName}/{s3_object_key}")
            with timer.phase("upload"):
                upload_result = s3.upload(stage_output.bucketName, s3_object_key, final_path)
            if upload_result is None:
                return _error_response(
                    stage,
                    f"Failed to upload preview to S3: {stage_output.bucketName}/{s3_object_key}",
                )
    except Exception as e:
        logger.exception(f"Failed to save/upload preview: {e}")
        return _error_response(stage, f"Failed to save/upload preview: {str(e)}")

    return _success_response(stage)


def _resolve_output_type(inputParametersObject: dict) -> str:
    """The thumbnail format the run writes, from the outputType input parameter.

    A value that names no format carries the auto default rather than failing the run: the
    parameter shipped for several releases as a comma-separated list of every format, so a
    configuration cloned from one keeps the automatic behaviour it already had.
    """
    if not isinstance(inputParametersObject, dict):
        return OUTPUT_TYPE_AUTO
    requested = inputParametersObject.get("outputType") or OUTPUT_TYPE_AUTO
    normalized = str(requested).strip().lower().lstrip(".")
    if normalized == "jpeg":
        normalized = "jpg"
    if not normalized:
        return OUTPUT_TYPE_AUTO
    if normalized not in OUTPUT_TYPES:
        logger.warning(f"outputType '{requested}' names no supported format, writing the "
                       f"automatic format. Supported: {', '.join(OUTPUT_TYPES)}")
        return OUTPUT_TYPE_AUTO
    return normalized


def _relative_subdir(stage_input: StageInput, stage_output: StageOutput, assetId: str) -> str:
    """The input file's subdirectory within the asset, so its preview is written beside it.

    A file an earlier workflow step produced or rewrote lives in the run's output FILES folder —
    the same folder this stage writes to — keyed by its asset-relative path, so the path after
    that prefix states where the file sits on the asset. An original asset file is keyed under its
    asset location instead, where the segments between the asset id and the filename are the
    subdirectory. The assetId is passed through the pipeline definition from the workflow state.

    Example: input key "pipelines/convert/job/output/exec/files/test/pump.glb" with output dir
      "pipelines/convert/job/output/exec/files/" → "test"
    Example: assetId "xd130a6d6", input key "xd130a6d6/a/b/model.glb" → "a/b"
    Example: input key "xd130a6d6/pump.e57" → ""
    """
    input_key = stage_input.objectKey or ""
    output_dir = stage_output.objectDir or ""
    if (input_key and output_dir and stage_output.bucketName
            and stage_input.bucketName == stage_output.bucketName
            and input_key.startswith(output_dir)):
        relative_path = input_key[len(output_dir):].strip("/")
        return relative_path.rsplit("/", 1)[0] if "/" in relative_path else ""

    if not assetId:
        logger.warning("No assetId provided in pipeline definition — cannot compute relative subdir")
        return ""
    if not input_key:
        return ""
    input_parts = input_key.split("/")
    try:
        asset_id_idx = input_parts.index(assetId)
    except ValueError:
        logger.warning(f"Asset ID '{assetId}' not found in input key '{input_key}'")
        return ""
    return "/".join(input_parts[asset_id_idx + 1:-1])


def _load_file(file_path: str, ext: str, asset_root: str = None, budgets=None):
    """
    Load a 3D file with the appropriate format handler.
    Returns a PyVista PolyData object. ``asset_root`` is the local directory the input's referenced
    files were fetched into (USD reads textures only from inside it). ``budgets`` bounds the points
    a point-cloud reader and the splats a splat reader keep, and the triangles a 3MF, COLLADA, STEP or
    FBX scene expands to.

    A ``.ply`` is routed by its header: Gaussian splats, a mesh, or a point cloud (vertices only). A
    ``.compressed.ply`` is a compressed splat scene.
    """
    max_points = budgets.points if budgets is not None else pointcloud_handler.MAX_POINTS_FOR_RENDER
    max_splats = budgets.splats if budgets is not None else splat_handler.DEFAULT_MAX_SPLATS
    max_triangles = budgets.triangles if budgets is not None else None
    work_dir = asset_root or os.path.dirname(os.path.abspath(file_path))
    if ext == ".ply":
        if splat_handler.is_compressed_ply(file_path):
            return splat_handler.load(file_path, max_splats=max_splats, work_dir=work_dir)
        kind = ply_header.ply_kind(file_path)
        logger.info(f"PLY content: {kind}")
        if kind == ply_header.KIND_SPLAT:
            return splat_handler.load_ply(file_path, max_splats)
        if kind == ply_header.KIND_COMPRESSED_SPLAT:
            return splat_handler.load(file_path, max_splats=max_splats, work_dir=work_dir,
                                      compressed_ply=True)
        if kind == ply_header.KIND_POINTS:
            return pointcloud_handler.load_ply_points(file_path, max_points)
        return mesh_handler.load(file_path)
    if splat_handler.can_handle(ext):
        return splat_handler.load(file_path, max_splats=max_splats, work_dir=work_dir)
    if xml_mesh_handler.can_handle(ext):
        return xml_mesh_handler.load(file_path, ext, max_triangles=max_triangles)
    if mesh_handler.can_handle(ext):
        return mesh_handler.load(file_path, max_triangles=max_triangles)
    elif pointcloud_handler.can_handle(ext):
        if budgets is None:
            return pointcloud_handler.load(file_path)
        return pointcloud_handler.load(file_path, max_points=budgets.points)
    elif cad_handler.can_handle(ext):
        return cad_handler.load(file_path, max_triangles=max_triangles)
    elif usd_handler.can_handle(ext):
        return usd_handler.load(file_path, asset_root=asset_root)
    else:
        raise ValueError(
            f"Unsupported file format: {ext}. "
            f"Supported formats: {', '.join(sorted(_all_extensions()))}"
        )


def _all_extensions():
    """Return all supported file extensions."""
    return (
        mesh_handler.SUPPORTED_EXTENSIONS
        | pointcloud_handler.SUPPORTED_EXTENSIONS
        | cad_handler.SUPPORTED_EXTENSIONS
        | usd_handler.SUPPORTED_EXTENSIONS
        | splat_handler.SUPPORTED_EXTENSIONS
        | xml_mesh_handler.SUPPORTED_EXTENSIONS
    )


def _find_local_test_file(input_dir: str) -> str:
    """Find the first supported 3D file in the local test input directory."""
    supported = _all_extensions()
    for entry in sorted(os.listdir(input_dir)):
        filepath = os.path.join(input_dir, entry)
        if os.path.isfile(filepath):
            ext = os.path.splitext(entry)[1].lower()
            if ext in supported:
                return filepath
    return None


# Format-based up-axis conventions
# Z-up: surveying/point cloud standards (LAS spec, E57 spec, Leica PTX/PTS)
_Z_UP_FORMATS = {".las", ".laz", ".e57", ".ptx", ".pts", ".pcd"}
# Y-up: glTF 2.0 mandates Y-up. STEP has no up-axis metadata and is rendered as authored. USD, 3MF and
# COLLADA handlers convert from their own metadata (stage upAxis, the 3MF spec's Z-up, <up_axis>), and
# the splat handler orients splat scenes, so all of these arrive Y-up.
_Y_UP_FORMATS = {".glb", ".gltf", ".stp", ".step", ".usd", ".usda", ".usdc", ".usdz", ".3mf", ".dae",
                 ".spz", ".sog", ".splat", ".ksplat", ".lcc"}
# No convention (.obj, .ply, .drc, .stl, .off; .fbx without UpAxis): rendered as authored, Y-up.


def _normalize_up_axis(pv_data, ext: str):
    """
    Normalize the up-axis to Y-up from what the file declares.

    Strategy:
      1. An up axis the loader read from the file (FBX ``UpAxis``) -> rotate when it is Z
      2. Known Y-up formats -> no-op
      3. Known Z-up formats -> rotate to Y-up
      4. Formats with no convention -> as authored. A bounding-box guess rotated Y-up models that are
         long in depth (a car, a ship) onto their side, and an unconditional STL rotation did the same
         to every Y-up STL.
    """
    ext = ext.lower()
    declared = getattr(pv_data, "_preview_up_axis", None)
    if declared == "Z":
        logger.info(f"File declares Z-up ({ext}), rotating to Y-up")
        return _rotate_z_up_to_y_up(pv_data)
    if declared == "Y":
        logger.info(f"File declares Y-up ({ext}), no rotation")
        return pv_data

    if ext in _Y_UP_FORMATS:
        logger.info(f"Format {ext} is Y-up (no rotation needed)")
        return pv_data

    if ext in _Z_UP_FORMATS:
        logger.info(f"Format {ext} is Z-up, rotating to Y-up")
        return _rotate_z_up_to_y_up(pv_data)

    logger.info(f"Format {ext} declares no up axis; rendering as authored (Y-up)")
    return pv_data


def _rotate_z_up_to_y_up(pv_data):
    """
    Rotate data from Z-up to Y-up by applying a -90 degree rotation around the X-axis.
    Transform: (x, y, z) → (x, z, -y)
    """
    points = pv_data.points.copy()
    new_points = np.empty_like(points)
    new_points[:, 0] = points[:, 0]    # X stays
    new_points[:, 1] = points[:, 2]    # Y = old Z (up axis)
    new_points[:, 2] = -points[:, 1]   # Z = -old Y
    pv_data.points = new_points
    logger.info("Applied Z-up → Y-up rotation")
    return pv_data


def _check_existing_preview(stage_input: StageInput, input_basename: str, localTest: bool) -> str:
    """
    Check if a preview file already exists for the given input file.
    Returns the path/key of the existing preview file, or None if not found.

    Preview files follow the naming pattern: <input_basename>.previewFile.<ext>
    where ext is gif, jpg, or png.

    A generated preview is written back beside its input file, so the check looks at the input
    file's own directory. The stage output directory is a per-execution prefix that never holds a
    prior run's previews.
    """
    preview_prefix = f"{input_basename}.previewFile."

    if localTest:
        # Check the input file's local directory
        input_dir = os.path.dirname(stage_input.objectKey) or "."
        if os.path.isdir(input_dir):
            for entry in os.listdir(input_dir):
                if entry.startswith(preview_prefix):
                    existing_path = os.path.join(input_dir, entry)
                    logger.info(f"Found existing local preview: {existing_path}")
                    return existing_path
    else:
        # Check the input file's directory in the asset bucket
        if stage_input.bucketName and stage_input.objectKey:
            input_dir = stage_input.objectKey.rsplit("/", 1)[0] + "/" if "/" in stage_input.objectKey else ""
            s3_prefix = input_dir + preview_prefix
            existing_keys = s3.list_objects_with_prefix(
                stage_input.bucketName, s3_prefix
            )
            if existing_keys:
                logger.info(f"Found existing S3 preview: {existing_keys[0]}")
                return existing_keys[0]

    return None


def _create_dir(parts: list) -> str:
    """Create a directory from path parts if it doesn't exist."""
    dir_path = os.path.join(*parts)
    if not os.path.exists(dir_path):
        Path(dir_path).mkdir(parents=True)
    return dir_path


def _success_response(stage: PipelineStage, message=None) -> PipelineStage:
    if not message:
        message = "Preview 3D Thumbnail pipeline executed successfully."
    logger.info(message)
    stage.status = PipelineStatus.COMPLETE
    return stage


def _error_response(stage: PipelineStage, message=None) -> PipelineStage:
    if not message:
        message = "Preview 3D Thumbnail pipeline failed. Check logs for details."
    logger.error(message)
    stage.errorMessage = message
    stage.status = PipelineStatus.FAILED
    return stage
