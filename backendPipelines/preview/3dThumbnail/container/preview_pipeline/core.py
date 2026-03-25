# Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Core pipeline orchestrator for Preview 3D Thumbnail generation.
Follows the pcPotreeViewer pipeline pattern: parse input, dispatch to handler, upload output.
"""

import os
import json
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
from .utils import image_utils
from .format_handlers import mesh_handler, pointcloud_handler, cad_handler, usd_handler
from . import renderer

logger = get_logger()

# Maximum input file size: 100 GB
MAX_INPUT_FILE_SIZE = 100 * 1024 * 1024 * 1024


def hello():
    logger.info("Preview 3D Thumbnail Pipeline - Generates preview images from 3D files")


def run(params: dict) -> PipelineExecutionParams:
    """
    Core runner for Preview 3D Thumbnail Pipeline.
    """
    # Convert input to data type
    definition = PipelineDefinition(**params)
    logger.info(f"Pipeline Definition: {definition}")

    # Set pipeline current stage
    if definition.currentStage is None:
        current_stage = PipelineStage(**definition.stages.pop(0))
        definition.currentStage = current_stage
        logger.info(f"Pipeline Current Stage: {current_stage}")
    else:
        current_stage = definition.currentStage

    # Verify pipeline type
    if current_stage.type != PipelineType.PREVIEW_3D_THUMBNAIL:
        logger.error(f"Pipeline Type {current_stage.type} not supported")
        output = PipelineExecutionParams(
            definition.jobName,
            current_stage.type,
            [definition.to_json()],
            definition.inputMetadata,
            definition.inputParameters,
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
            definition.inputMetadata,
            definition.inputParameters,
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
        definition.inputMetadata,
        definition.inputParameters,
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
    inputMetadata: str = "",
    inputParameters: str = "",
    localTest: bool = False,
    assetId: str = "",
) -> PipelineStage:
    """
    Run the preview generation pipeline for a single stage.

    1. Download input file from S3
    2. Detect format and load with appropriate handler
    3. Render rotating preview frames
    4. Save as GIF (with size optimization / JPEG fallback)
    5. Upload to S3 output directory
    """
    # Parse input parameters
    inputParametersObject = {}
    if isinstance(inputParameters, str) and inputParameters != "":
        try:
            inputParametersObject = json.loads(inputParameters)
        except Exception:
            logger.error("Input parameters is not valid JSON.")

    # Create local working directories
    local_input_dir = _create_dir(["tmp", "input"])
    local_output_dir = _create_dir(["tmp", "output"])

    logger.info("Running Preview 3D Thumbnail Pipeline...")
    logger.info(f"Stage: {stage}")

    # Get pipeline stage input and output
    stage_input = StageInput(**stage.inputFile)
    stage_output = StageOutput(**stage.outputFiles)

    # Compute relative subdirectory from the input object key so the output
    # preserves the same directory structure within the asset.
    # The assetId is passed through the pipeline definition from the workflow state.
    # We find the assetId in the input key, then everything after it
    # (minus the filename) is the relative subdirectory.
    # Example: assetId = "xd130a6d6...", input key = "xd130a6d6.../test/pump.e57"
    #   → relative_subdir = "test"
    # Example: input key = "xd130a6d6.../a/b/model.glb" → relative_subdir = "a/b"
    # Example: input key = "xd130a6d6.../pump.e57" → relative_subdir = ""
    relative_subdir = ""
    if assetId and stage_input.objectKey:
        input_parts = stage_input.objectKey.split("/")
        try:
            asset_id_idx = input_parts.index(assetId)
            # Everything between the asset ID and the filename is the relative subdir
            if asset_id_idx + 1 < len(input_parts) - 1:
                relative_subdir = "/".join(input_parts[asset_id_idx + 1:-1])
        except ValueError:
            logger.warning(f"Asset ID '{assetId}' not found in input key '{stage_input.objectKey}'")
    elif not assetId:
        logger.warning("No assetId provided in pipeline definition — cannot compute relative subdir")
    logger.info(f"Input relative subdirectory: '{relative_subdir}'")

    # Parse overwriteExistingPreviewFiles parameter (default: False)
    overwrite_existing = False
    if isinstance(inputParametersObject, dict):
        overwrite_existing = inputParametersObject.get("overwriteExistingPreviewFiles", False)
        if not isinstance(overwrite_existing, bool):
            overwrite_existing = str(overwrite_existing).lower() in ("true", "1", "yes")
    logger.info(f"overwriteExistingPreviewFiles: {overwrite_existing}")

    # Check if a preview file already exists for this input file
    input_basename = os.path.basename(stage_input.objectKey) if stage_input.objectKey else ""
    if input_basename:
        existing_preview = _check_existing_preview(
            stage_output, input_basename, localTest, relative_subdir
        )
        if existing_preview and not overwrite_existing:
            return _error_response(
                stage,
                f"A preview file already exists for '{input_basename}': {existing_preview}. "
                f"Set inputParameters.overwriteExistingPreviewFiles to true to overwrite.",
            )
        elif existing_preview and overwrite_existing:
            logger.info(
                f"Existing preview found ({existing_preview}), overwriting as requested."
            )

    # Download file from S3
    if localTest:
        # localTest mode: use file from /data/input/ volume mount
        if stage_input.objectKey and stage_input.objectKey != "":
            # Specific file path provided via CLI argument
            local_filepath = stage_input.objectKey
        else:
            # Find first supported file in /data/input/
            local_filepath = _find_local_test_file("/data/input")
        logger.info(f"Using local test file: {local_filepath}")

        # Check file size in localTest mode
        if local_filepath and os.path.isfile(local_filepath):
            file_size = os.path.getsize(local_filepath)
            if file_size > MAX_INPUT_FILE_SIZE:
                return _error_response(
                    stage,
                    f"Input file size ({file_size / (1024**3):.2f} GB) exceeds "
                    f"maximum allowed size ({MAX_INPUT_FILE_SIZE / (1024**3):.0f} GB).",
                )
    else:
        # Check file size in S3 before downloading
        file_size = s3.get_object_size(stage_input.bucketName, stage_input.objectKey)
        if file_size is None:
            logger.warning(
                "Unable to determine file size via HEAD request. "
                "Proceeding with download — size limit will not be enforced."
            )
        elif file_size > MAX_INPUT_FILE_SIZE:
            return _error_response(
                stage,
                f"Input file size ({file_size / (1024**3):.2f} GB) exceeds "
                f"maximum allowed size ({MAX_INPUT_FILE_SIZE / (1024**3):.0f} GB).",
            )

        logger.info(f"Downloading file from S3: {stage_input.bucketName}/{stage_input.objectKey}")
        local_filepath = s3.download(
            stage_input.bucketName,
            stage_input.objectKey,
            os.path.join(local_input_dir, os.path.basename(stage_input.objectKey)),
        )

    # Verify download
    if local_filepath is None or not os.path.isfile(local_filepath):
        return _error_response(
            stage,
            "Unable to download file from S3 and/or no input file provided. "
            "Check bucket name, object key, and local input parameters.",
        )

    # Detect format
    ext = os.path.splitext(local_filepath)[1].lower()
    if stage_input.fileExtension:
        ext = "." + stage_input.fileExtension.lower().lstrip(".")

    logger.info(f"Detected file extension: {ext}")

    # For GLTF files in S3 mode, download external dependencies (buffers, textures)
    if not localTest and ext == ".gltf":
        try:
            _download_gltf_dependencies(
                stage_input.bucketName,
                stage_input.objectKey,
                local_input_dir,
            )
        except Exception as e:
            logger.warning(f"Failed to download some GLTF dependencies: {e}")

    # Load with appropriate handler
    try:
        pv_data = _load_file(local_filepath, ext)
    except Exception as e:
        logger.exception(f"Failed to load file: {e}")
        return _error_response(stage, f"Failed to load 3D file ({ext}): {str(e)}")

    # Detect and normalize up-axis to Y-up for consistent rendering
    try:
        pv_data = _normalize_up_axis(pv_data, ext)
    except Exception as e:
        logger.warning(f"Up-axis normalization failed, rendering with original orientation: {e}")

    # Generate preview frames
    # For USD and CAD files, use full bounding box (no percentile cropping)
    # since these are engineered models where all geometry is intentional
    _full_bounds_exts = {'.usd', '.usda', '.usdc', '.usdz', '.stp', '.step'}
    use_full_bounds = ext.lower() in _full_bounds_exts
    try:
        frames = renderer.generate_rotating_frames(pv_data, use_full_bounds=use_full_bounds)
    except Exception as e:
        logger.exception(f"Failed to render frames: {e}")
        # Fall back to static frame
        try:
            logger.info("Attempting static frame fallback...")
            static_img = renderer.generate_static_frame(pv_data, use_full_bounds=use_full_bounds)
            frames = [static_img]
        except Exception as e2:
            logger.exception(f"Static frame fallback also failed: {e2}")
            return _error_response(stage, f"Failed to render preview: {str(e)}")

    # Save and optimize output
    try:
        # Use actual file basename (from local_filepath for localTest, objectKey for S3)
        input_basename = os.path.basename(local_filepath)
        if len(frames) > 1:
            output_filename = f"{input_basename}.previewFile.gif"
        else:
            output_filename = f"{input_basename}.previewFile.jpg"

        output_path = os.path.join(local_output_dir, output_filename)

        if len(frames) > 1:
            final_path = image_utils.ensure_under_size_limit(frames, output_path)
        else:
            image_utils.save_jpeg(frames[0], output_path)
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


def _load_file(file_path: str, ext: str):
    """
    Load a 3D file with the appropriate format handler.
    Returns a PyVista PolyData object.
    """
    if mesh_handler.can_handle(ext):
        return mesh_handler.load(file_path)
    elif pointcloud_handler.can_handle(ext):
        return pointcloud_handler.load(file_path)
    elif cad_handler.can_handle(ext):
        return cad_handler.load(file_path)
    elif usd_handler.can_handle(ext):
        return usd_handler.load(file_path)
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
# Z-up: surveying/point cloud standards (LAS spec, E57 spec)
_Z_UP_FORMATS = {".las", ".laz", ".e57", ".ptx", ".pcd", ".fls", ".fws", ".stl"}
# Y-up: glTF 2.0 spec mandates Y-up; STEP/STP files are treated as Y-up because
# cadquery tessellation preserves original model coordinates which render correctly as-is;
# USD files handle up-axis internally via stage metadata (UsdGeom.GetStageUpAxis)
_Y_UP_FORMATS = {".glb", ".gltf", ".stp", ".step", ".usd", ".usda", ".usdc", ".usdz"}
# Variable (use heuristic): .obj, .fbx, .ply, .drc


def _normalize_up_axis(pv_data, ext: str):
    """
    Detect and normalize the up-axis to Y-up for consistent rendering.

    Strategy:
      1. Known Z-up formats → rotate to Y-up
      2. Known Y-up formats → no-op
      3. Variable formats → bounding-box heuristic
    """
    ext = ext.lower()

    if ext in _Y_UP_FORMATS:
        logger.info(f"Format {ext} is Y-up (no rotation needed)")
        return pv_data

    if ext in _Z_UP_FORMATS:
        logger.info(f"Format {ext} is Z-up, rotating to Y-up")
        return _rotate_z_up_to_y_up(pv_data)

    # Variable format — use bounding-box heuristic
    return _heuristic_up_axis(pv_data, ext)


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


def _heuristic_up_axis(pv_data, ext: str):
    """
    Detect up-axis from bounding box geometry for formats without a fixed convention.
    If the Z extent is notably larger than the Y extent, the data is likely Z-up.
    """
    bounds = pv_data.bounds  # (xmin, xmax, ymin, ymax, zmin, zmax)
    dy = bounds[3] - bounds[2]
    dz = bounds[5] - bounds[4]

    if dz > dy * 1.2:
        logger.info(
            f"Heuristic ({ext}): Z extent ({dz:.2f}) > Y extent ({dy:.2f}) * 1.2, "
            f"assuming Z-up → rotating to Y-up"
        )
        return _rotate_z_up_to_y_up(pv_data)

    logger.info(
        f"Heuristic ({ext}): Y extent ({dy:.2f}) >= Z extent ({dz:.2f}), "
        f"assuming Y-up (no rotation)"
    )
    return pv_data


def _download_gltf_dependencies(bucket_name: str, object_key: str, local_dir: str):
    """
    Parse a .gltf file and download external resources (buffers, images/textures)
    from the same S3 directory so trimesh can resolve them at load time.
    """
    local_gltf = os.path.join(local_dir, os.path.basename(object_key))

    with open(local_gltf, "r") as f:
        gltf = json.load(f)

    # S3 directory containing the .gltf file
    s3_dir = os.path.dirname(object_key)

    # Collect all external URIs (skip embedded data URIs)
    uris = set()
    for buf in gltf.get("buffers", []):
        uri = buf.get("uri", "")
        if uri and not uri.startswith("data:"):
            uris.add(uri)
    for img in gltf.get("images", []):
        uri = img.get("uri", "")
        if uri and not uri.startswith("data:"):
            uris.add(uri)

    if not uris:
        logger.info("GLTF file has no external dependencies")
        return

    logger.info(f"Downloading {len(uris)} GLTF dependencies from S3")
    for uri in sorted(uris):
        s3_key = f"{s3_dir}/{uri}" if s3_dir else uri
        local_path = os.path.join(local_dir, uri)
        # Create subdirectories if the URI has path components
        parent = os.path.dirname(local_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        logger.info(f"  Downloading: {s3_key}")
        s3.download(bucket_name, s3_key, local_path)


def _check_existing_preview(stage_output: StageOutput, input_basename: str, localTest: bool, relative_subdir: str = "") -> str:
    """
    Check if a preview file already exists for the given input file.
    Returns the path/key of the existing preview file, or None if not found.

    Preview files follow the naming pattern: <input_basename>.previewFile.<ext>
    where ext is gif, jpg, or png.
    """
    preview_prefix = f"{input_basename}.previewFile."

    if localTest:
        # Check local output directory (with relative subdir if present)
        output_dir = os.path.join(stage_output.objectDir, relative_subdir) if relative_subdir else stage_output.objectDir
        if os.path.isdir(output_dir):
            for entry in os.listdir(output_dir):
                if entry.startswith(preview_prefix):
                    existing_path = os.path.join(output_dir, entry)
                    logger.info(f"Found existing local preview: {existing_path}")
                    return existing_path
    else:
        # Check S3 output directory (with relative subdir if present)
        if stage_output.bucketName and stage_output.objectDir:
            if relative_subdir:
                s3_prefix = stage_output.objectDir + relative_subdir + "/" + preview_prefix
            else:
                s3_prefix = stage_output.objectDir + preview_prefix
            existing_keys = s3.list_objects_with_prefix(
                stage_output.bucketName, s3_prefix
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
