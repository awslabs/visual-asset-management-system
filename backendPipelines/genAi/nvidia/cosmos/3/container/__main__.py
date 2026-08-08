"""
VAMS Cosmos 3 (omni) Container Wrapper

Orchestrates: load definition -> ensure models cached (HF_HOME on EFS + S3
backup) -> download input file (image2video) -> run inference via
cosmos-framework -> upload outputs to S3.

Container handles inference and S3 I/O only. SFN task callbacks are handled by
the pipelineEnd Lambda. Container exits 0 on success, non-zero on failure.
"""

import json
import logging
import os
import sys
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

from inference import generate_preview_gif, run_inference
from model_manager import ensure_models_cached
import manifest_io

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

INPUT_DIR = Path("/tmp/input")
OUTPUT_DIR = Path("/tmp/output")
HF_CACHE_BASE = "/mnt/efs/cosmos-models/hf_cache"

# Task modes that consume an input file
INPUT_FILE_MODES = ("image2video", "video2video", "transfer")
# Task modes that emit a still image (not video)
IMAGE_OUTPUT_MODES = ("text2image",)
# Variants that can perform control-signal transfer (general-purpose omni models)
TRANSFER_CAPABLE_VARIANTS = ("nano", "super")
# Supported control-signal types for transfer
TRANSFER_CONTROL_TYPES = ("edge", "blur", "depth", "seg", "wsm")


def build_control_blocks(control_types_raw, control_paths_raw, control_weights_raw):
    """Build the per-control-type block mapping for control-signal transfer.

    Each of the raw inputs is a comma-separated string (multi-control), aligned
    by position. control_paths entries may be blank (auto-compute from source).
    Returns {control_type: {"weight": float[, "control_path": str]}}.
    """
    types = [t.strip().lower() for t in str(control_types_raw or "").split(",") if t.strip()]
    if not types:
        types = ["edge"]
    paths = [p.strip() for p in str(control_paths_raw or "").split(",")]
    weights = [w.strip() for w in str(control_weights_raw or "").split(",")]

    blocks = {}
    for i, ctype in enumerate(types):
        if ctype not in TRANSFER_CONTROL_TYPES:
            logger.warning(f"Unknown control type '{ctype}' ignored (supported: {TRANSFER_CONTROL_TYPES})")
            continue
        block = {}
        # Weight: use the aligned entry if present and non-blank, else default 1.0
        try:
            block["weight"] = float(weights[i]) if i < len(weights) and weights[i] != "" else 1.0
        except ValueError:
            block["weight"] = 1.0
        blocks[ctype] = block
    return blocks, types, paths


def load_pipeline_definition() -> Dict:
    definition_source = None
    if len(sys.argv) > 1:
        definition_source = sys.argv[1]
    elif "PIPELINE_DEFINITION" in os.environ:
        definition_source = os.environ["PIPELINE_DEFINITION"]
    else:
        raise ValueError("No pipeline definition provided via command line or PIPELINE_DEFINITION env var")
    logger.info(f"Loading pipeline definition from: {definition_source[:100]}...")
    try:
        return json.loads(definition_source)
    except json.JSONDecodeError:
        pass
    definition_path = Path(definition_source)
    if definition_path.exists():
        with open(definition_path, "r") as f:
            return json.load(f)
    raise ValueError(f"Could not parse pipeline definition: {definition_source}")


def parse_s3_uri(s3_uri: str) -> Tuple[str, str]:
    parsed = urlparse(s3_uri)
    return parsed.netloc, parsed.path.lstrip("/")


def download_from_s3(s3_uri: str, local_path: Path) -> None:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"Downloading from S3: {s3_uri} -> {local_path}")
    result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
        ["aws", "s3", "cp", s3_uri, str(local_path)], capture_output=True, text=True
    )  # nosemgrep: dangerous-subprocess-use-audit
    if result.returncode != 0:
        raise RuntimeError(f"S3 download failed: {result.stderr}")


def upload_to_s3(local_path: Path, s3_uri: str) -> None:
    logger.info(f"Uploading to S3: {local_path} -> {s3_uri}")
    result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
        ["aws", "s3", "cp", str(local_path), s3_uri], capture_output=True, text=True
    )  # nosemgrep: dangerous-subprocess-use-audit
    if result.returncode != 0:
        raise RuntimeError(f"S3 upload failed: {result.stderr}")


def compute_relative_subdir(input_s3_path: str, asset_id: str) -> str:
    _, key = parse_s3_uri(input_s3_path)
    parts = key.split("/")
    try:
        asset_id_idx = parts.index(asset_id)
    except ValueError:
        logger.warning(f"Asset ID {asset_id} not found in S3 path {input_s3_path}")
        return ""
    subdir_parts = parts[asset_id_idx + 1:-1]
    return "/".join(subdir_parts) + "/" if subdir_parts else ""


def find_output_file(output_dir: Path, extensions) -> Optional[Path]:
    for ext in extensions:
        for f in output_dir.rglob(f"*{ext}"):
            logger.info(f"Found output file: {f}")
            return f
    logger.warning(f"No {extensions} files found in {output_dir}")
    return None


def main():
    start_time = time.time()
    try:
        logger.info("=" * 80)
        logger.info("VAMS Cosmos 3 Pipeline Starting")
        logger.info("=" * 80)

        definition = load_pipeline_definition()
        logger.info(f"Pipeline definition loaded: {json.dumps(definition, indent=2)}")

        variant = definition.get("modelVariant") or os.environ.get("MODEL_VARIANT", "nano")
        task_mode = definition.get("taskMode") or os.environ.get("TASK_MODE", "")
        cosmos_prompt = definition.get("cosmosPrompt", "")
        negative_prompt = definition.get("cosmosNegativePrompt", "")
        seed = int(definition.get("cosmosSeed", 0) or 0)
        guidance_raw = definition.get("cosmosGuidance", "")
        num_frames = int(definition.get("cosmosNumFrames", 189) or 189)
        # Control-signal transfer fields (only used when task_mode == "transfer")
        control_type_raw = definition.get("cosmosControlType", "")
        control_path_raw = definition.get("cosmosControlPath", "")
        control_weight_raw = definition.get("cosmosControlWeight", "")
        control_guidance_raw = definition.get("cosmosControlGuidance", "")
        input_s3_asset_file_path = definition.get("inputS3AssetFilePath")
        output_s3_asset_files_path = definition.get("outputS3AssetFilesPath")
        asset_id = definition.get("assetId")

        hf_token = os.environ.get("HF_TOKEN")
        s3_model_bucket = os.environ.get("S3_MODEL_BUCKET")
        num_gpus = int(os.environ.get("NUM_GPUS", "1"))

        hf_home = HF_CACHE_BASE
        os.environ["HF_HOME"] = hf_home
        if hf_token:
            os.environ["HF_TOKEN"] = hf_token

        # Optional flags from the input configuration. Only the S3 LOCATION travels in the pipeline
        # definition; the container reads the configuration file from S3 here (inline fallback for
        # local-test invocations that pass a raw JSON string instead of an s3:// location).
        invalidate_models = False
        disable_guardrails = True
        generate_preview_gif_flag = False
        try:
            params = manifest_io.fetch_input_configuration(
                definition.get("inputConfigurationS3Location", ""))
            if params:
                invalidate_models = str(params.get("INVALIDATE_COSMOS_MODELS", "")).lower() == "true"
                disable_guardrails = str(params.get("DISABLE_GUARDRAILS", "true")).lower() != "false"
                generate_preview_gif_flag = str(params.get("GENERATE_PREVIEW_GIF", "")).lower() == "true"
                if not task_mode:
                    task_mode = params.get("TASK_MODE", "") or task_mode
                if not variant or variant == "nano":
                    variant = params.get("MODEL_VARIANT", variant) or variant
        # A configuration that EXISTS but cannot be parsed is not something to tolerate: the
        # broad handler below would leave the run on its defaults and still report success,
        # with every caller-supplied parameter silently dropped. Placed ABOVE that handler --
        # below it this arm would be dead code.
        except manifest_io.InputConfigurationError:
            raise
        except Exception:
            pass

        if not s3_model_bucket:
            raise ValueError("S3_MODEL_BUCKET environment variable is required")
        if not asset_id:
            raise ValueError("assetId is required in pipeline definition")
        if not output_s3_asset_files_path:
            raise ValueError("outputS3AssetFilesPath is required in pipeline definition")

        guidance = float(guidance_raw) if str(guidance_raw).strip() != "" else None

        # Control-signal transfer is only supported on the general-purpose omni
        # checkpoints (nano, super). Ignore the transfer request for the
        # task-specialized Super variants and fall back to their normal mode.
        if task_mode == "transfer" and variant not in TRANSFER_CAPABLE_VARIANTS:
            logger.warning(
                f"task_mode=transfer is not supported for variant '{variant}'; "
                f"ignoring transfer and using the variant's default mode"
            )
            task_mode = ""

        is_transfer = task_mode == "transfer"
        effective_mode = task_mode or ""

        logger.info(f"Variant: {variant}, task_mode: {task_mode}, seed: {seed}, num_frames: {num_frames}")
        logger.info(f"Prompt: {cosmos_prompt}")

        # Step 1: Ensure models cached
        logger.info("Step 1: Ensuring models are cached")
        ensure_models_cached(hf_home=hf_home, s3_bucket=s3_model_bucket, invalidate=invalidate_models)

        # Step 2: Download input file for input-file modes
        input_file_path = None
        # Determine effective mode for input decision: fall back to variant default handled in inference
        needs_input = effective_mode in INPUT_FILE_MODES or variant == "super-image2video"
        if needs_input:
            if not input_s3_asset_file_path:
                raise ValueError(f"Mode {effective_mode or variant} requires inputS3AssetFilePath")
            logger.info("Step 2: Downloading input file")
            input_filename = Path(parse_s3_uri(input_s3_asset_file_path)[1]).name
            input_file_path = INPUT_DIR / input_filename
            download_from_s3(input_s3_asset_file_path, input_file_path)

        # Step 2b: For transfer, build control blocks and download any pre-computed
        # control videos. A blank control path leaves the framework to auto-compute
        # the signal from the source video (vision_path).
        control_blocks = None
        control_guidance = None
        if is_transfer:
            control_blocks, control_types, control_paths = build_control_blocks(
                control_type_raw, control_path_raw, control_weight_raw
            )
            if not control_blocks:
                raise ValueError(
                    f"transfer requires at least one valid COSMOS3_CONTROL_TYPE "
                    f"(supported: {', '.join(TRANSFER_CONTROL_TYPES)})"
                )
            control_guidance = (
                float(control_guidance_raw) if str(control_guidance_raw).strip() != "" else 1.5
            )
            logger.info("Step 2b: Preparing transfer control signals")
            for i, ctype in enumerate(control_types):
                if ctype not in control_blocks:
                    continue
                s3_path = control_paths[i] if i < len(control_paths) else ""
                if s3_path:
                    control_local = INPUT_DIR / f"control_{ctype}_{Path(parse_s3_uri(s3_path)[1]).name}"
                    download_from_s3(s3_path, control_local)
                    control_blocks[ctype]["control_path"] = str(control_local)
                    logger.info(f"  {ctype}: using pre-computed control {s3_path}")
                else:
                    logger.info(f"  {ctype}: auto-computed from source video")

        # Step 3: Run inference
        logger.info("Step 3: Running inference")
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        run_inference(
            variant=variant,
            task_mode=task_mode,
            prompt=cosmos_prompt,
            negative_prompt=negative_prompt,
            num_frames=num_frames,
            guidance=guidance,
            seed=seed,
            input_file_path=str(input_file_path) if input_file_path else None,
            output_dir=str(OUTPUT_DIR),
            hf_home=hf_home,
            hf_token=hf_token,
            num_gpus=num_gpus,
            disable_guardrails=disable_guardrails,
            control_blocks=control_blocks,
            control_guidance=control_guidance,
        )

        # Step 4: Find output (image for text2image, else video)
        logger.info("Step 4: Finding output file")
        is_image_output = (task_mode in IMAGE_OUTPUT_MODES) or variant == "super-text2image"
        if is_image_output:
            output_file = find_output_file(OUTPUT_DIR, (".png", ".jpg", ".jpeg", ".webp"))
            out_ext = output_file.suffix if output_file else ".png"
        else:
            output_file = find_output_file(OUTPUT_DIR, (".mp4",))
            out_ext = ".mp4"
        if not output_file:
            raise RuntimeError("No output file generated")

        # Step 5: Upload to S3, preserving relative path for input-file modes
        logger.info("Step 5: Uploading output to S3")
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_bucket, output_base = parse_s3_uri(output_s3_asset_files_path)
        output_base = output_base.rstrip("/") + "/"
        if needs_input and input_s3_asset_file_path:
            relative_subdir = compute_relative_subdir(input_s3_asset_file_path, asset_id)
            stem = Path(parse_s3_uri(input_s3_asset_file_path)[1]).stem
            output_filename = f"{stem}_Cosmos3_{variant}_{timestamp}{out_ext}"
            output_key = f"{output_base}{relative_subdir}{output_filename}"
        else:
            output_filename = f"cosmos3-{variant}-{timestamp}{out_ext}"
            output_key = f"{output_base}{output_filename}"
        output_s3_uri = f"s3://{output_bucket}/{output_key}"
        upload_to_s3(output_file, output_s3_uri)
        logger.info(f"Uploaded output: {output_s3_uri}")

        # Step 6: Optional preview GIF for video outputs
        if generate_preview_gif_flag and out_ext == ".mp4":
            logger.info("Step 6: Generating and uploading preview GIF")
            try:
                preview_gif_path = OUTPUT_DIR / "preview.gif"
                generate_preview_gif(video_path=str(output_file), output_path=str(preview_gif_path))
                preview_s3_uri = f"s3://{output_bucket}/{output_key}.previewFile.gif"
                upload_to_s3(preview_gif_path, preview_s3_uri)
                logger.info(f"Preview GIF uploaded: {preview_s3_uri}")
            except Exception as gif_err:
                logger.warning(f"Failed to generate/upload preview GIF (non-fatal): {gif_err}")

        # Step 7: Backup HF cache (non-fatal)
        logger.info("Step 7: Backing up HF cache to S3")
        try:
            from model_manager import backup_cache_to_s3
            backup_cache_to_s3(hf_home=hf_home, s3_bucket=s3_model_bucket)
        except Exception as backup_err:
            logger.warning(f"Failed to backup HF cache to S3 (non-fatal): {backup_err}")

        elapsed = time.time() - start_time
        logger.info(f"Pipeline completed successfully in {elapsed:.1f}s. Output: {output_s3_uri}")

    except Exception as e:
        logger.error("Pipeline failed with error:")
        logger.error(str(e))
        import traceback
        logger.error(traceback.format_exc())
        sys.stdout.flush()
        sys.stderr.flush()
        raise


if __name__ == "__main__":
    main()
