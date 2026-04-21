"""
VAMS Cosmos Transfer 2.5 Container Wrapper

Orchestrates the full pipeline:
1. Load pipeline definition
2. Ensure models are cached (HF_HOME on EFS, with S3 backup)
3. Download source video from S3
4. Download control signal video from S3 (optional)
5. Run inference via cosmos-transfer2.5 examples/inference.py
6. Upload output video to S3

Container handles inference and S3 I/O only. SFN task callbacks are
handled by the pipelineEnd Lambda (triggered by the SFN state machine
after the Batch job completes). Container exits with code 0 on success
or non-zero on failure.
"""

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

from inference import run_inference
from model_manager import ensure_models_cached

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# No boto3 needed -- S3 operations use AWS CLI subprocess, SFN callbacks handled by pipelineEnd Lambda


# Directories
INPUT_DIR = Path("/tmp/input")
OUTPUT_DIR = Path("/tmp/output")
HF_CACHE_BASE = "/mnt/efs/cosmos-models/hf_cache"


def load_pipeline_definition() -> Dict:
    """
    Load pipeline definition from command line argument or environment variable.

    Supports:
    - sys.argv[1]: JSON string or file path
    - PIPELINE_DEFINITION env var: JSON string or file path

    Returns:
        Pipeline definition dict

    Raises:
        ValueError: If definition cannot be loaded
    """
    definition_source = None

    # Try command line argument
    if len(sys.argv) > 1:
        definition_source = sys.argv[1]
    # Try environment variable
    elif "PIPELINE_DEFINITION" in os.environ:
        definition_source = os.environ["PIPELINE_DEFINITION"]
    else:
        raise ValueError("No pipeline definition provided via command line or PIPELINE_DEFINITION env var")

    logger.info(f"Loading pipeline definition from: {definition_source[:100]}...")

    # Try parsing as JSON string first
    try:
        return json.loads(definition_source)
    except json.JSONDecodeError:
        pass

    # Try loading as file path
    try:
        definition_path = Path(definition_source)
        if definition_path.exists():
            with open(definition_path, "r") as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load as file: {e}")

    raise ValueError(f"Could not parse pipeline definition as JSON or load from file: {definition_source}")


def parse_s3_uri(s3_uri: str) -> Tuple[str, str]:
    """
    Parse S3 URI into bucket and key.

    Args:
        s3_uri: S3 URI (s3://bucket/key)

    Returns:
        Tuple of (bucket, key)
    """
    parsed = urlparse(s3_uri)
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    return bucket, key


def download_from_s3(s3_uri: str, local_path: Path) -> None:
    """
    Download file from S3 using AWS CLI.

    Args:
        s3_uri: S3 URI (s3://bucket/key)
        local_path: Local file path to save to
    """
    local_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(f"Downloading from S3: {s3_uri} -> {local_path}")
    result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
        ["aws", "s3", "cp", s3_uri, str(local_path)],
        capture_output=True, text=True
    )  # nosemgrep: dangerous-subprocess-use-audit
    if result.returncode != 0:
        raise RuntimeError(f"S3 download failed: {result.stderr}")
    logger.info(f"Downloaded {local_path.stat().st_size} bytes from S3")


def upload_to_s3(local_path: Path, s3_uri: str) -> None:
    """
    Upload file to S3 using AWS CLI.

    Args:
        local_path: Local file path to upload
        s3_uri: S3 URI (s3://bucket/key)
    """
    logger.info(f"Uploading to S3: {local_path} -> {s3_uri}")
    result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
        ["aws", "s3", "cp", str(local_path), s3_uri],
        capture_output=True, text=True
    )  # nosemgrep: dangerous-subprocess-use-audit
    if result.returncode != 0:
        raise RuntimeError(f"S3 upload failed: {result.stderr}")
    logger.info(f"Uploaded {local_path.stat().st_size} bytes to S3")


def compute_relative_subdir(input_s3_path: str, asset_id: str) -> str:
    """
    Extract relative path between assetId and filename in S3 key.

    Example:
        input_s3_path: s3://bucket/xd130a6d6.../test/pump.mp4
        asset_id: xd130a6d6...
        -> "test/"

    Args:
        input_s3_path: Full S3 URI of input file
        asset_id: Asset ID

    Returns:
        Relative subdirectory path (with trailing slash, or empty string)
    """
    _, key = parse_s3_uri(input_s3_path)
    parts = key.split("/")

    # Find asset_id in path
    try:
        asset_id_idx = parts.index(asset_id)
    except ValueError:
        logger.warning(f"Asset ID {asset_id} not found in S3 path {input_s3_path}")
        return ""

    # Extract subdirectory between asset_id and filename
    subdir_parts = parts[asset_id_idx + 1:-1]

    if subdir_parts:
        return "/".join(subdir_parts) + "/"
    return ""


def find_output_video(output_dir: Path) -> Optional[Path]:
    """
    Find output video file in output directory.

    Cosmos Transfer 2.5 writes output to a subdirectory structure under the
    output dir. Search recursively for .mp4 files.

    Args:
        output_dir: Directory to search

    Returns:
        Path to first .mp4 file found, or None
    """
    for video_path in output_dir.rglob("*.mp4"):
        logger.info(f"Found output video: {video_path}")
        return video_path

    logger.warning(f"No .mp4 files found in {output_dir}")
    return None


def main():
    """Main pipeline execution.

    Container handles inference and S3 I/O only. SFN task callbacks are
    handled by the pipelineEnd Lambda (triggered by the SFN state machine
    after the Batch job completes). Container exits with code 0 on success
    or non-zero on failure.
    """
    start_time = time.time()

    try:
        # Load pipeline definition
        logger.info("=" * 80)
        logger.info("VAMS Cosmos Transfer 2.5 Pipeline Starting")
        logger.info("=" * 80)

        definition = load_pipeline_definition()
        logger.info(f"Pipeline definition loaded: {json.dumps(definition, indent=2)}")

        # Extract required fields
        model_type = definition.get("modelType", "transfer")
        cosmos_prompt = definition.get("cosmosPrompt", "")
        input_parameters_prompt = definition.get("inputParametersPrompt")
        input_s3_asset_file_path = definition.get("inputS3AssetFilePath")
        output_s3_asset_files_path = definition.get("outputS3AssetFilesPath")
        asset_id = definition.get("assetId")
        control_type = definition.get("controlType", "edge")
        control_path = definition.get("controlPath", "")

        # Get environment variables
        hf_token = os.environ.get("HF_TOKEN")
        s3_model_bucket = os.environ.get("S3_MODEL_BUCKET")

        # Set HF_HOME for native HuggingFace cache on EFS
        hf_home = HF_CACHE_BASE
        os.environ["HF_HOME"] = hf_home

        if hf_token:
            os.environ["HF_TOKEN"] = hf_token

        # Check for optional flags from inputParameters
        disable_guardrails = True
        control_weight = 1.0
        try:
            input_params = definition.get("inputParameters", "")
            if input_params:
                params = json.loads(input_params) if isinstance(input_params, str) else input_params
                disable_guardrails = str(params.get("DISABLE_GUARDRAILS", "true")).lower() != "false"
                control_weight = float(params.get("CONTROL_WEIGHT", "1.0"))
                if not disable_guardrails:
                    logger.info("DISABLE_GUARDRAILS=false: guardrails will be enabled")
                logger.info(f"Control weight: {control_weight}")
        except Exception:
            pass

        if not s3_model_bucket:
            raise ValueError("S3_MODEL_BUCKET environment variable is required")

        if not asset_id:
            raise ValueError("assetId is required in pipeline definition")

        if not output_s3_asset_files_path:
            raise ValueError("outputS3AssetFilesPath is required in pipeline definition")

        if not input_s3_asset_file_path:
            raise ValueError("inputS3AssetFilePath is required for transfer pipeline")

        # Determine prompt (cosmosPrompt takes precedence)
        prompt = cosmos_prompt if cosmos_prompt else (input_parameters_prompt or "Transform the video")

        logger.info(f"Model type: {model_type}")
        logger.info(f"Control type: {control_type}")
        logger.info(f"Control path: {control_path or '(on-the-fly computation)'}")
        logger.info(f"Prompt: {prompt}")
        logger.info(f"Input file: {input_s3_asset_file_path}")
        logger.info(f"Output path: {output_s3_asset_files_path}")
        logger.info(f"Asset ID: {asset_id}")
        logger.info(f"HF_HOME: {hf_home}")

        # Step 1: Ensure models are cached
        logger.info("=" * 80)
        logger.info("Step 1: Ensuring models are cached")
        logger.info("=" * 80)

        ensure_models_cached(
            hf_home=hf_home,
            s3_bucket=s3_model_bucket,
            invalidate=False
        )

        # Step 2: Download source video
        logger.info("=" * 80)
        logger.info("Step 2: Downloading source video")
        logger.info("=" * 80)

        input_filename = Path(parse_s3_uri(input_s3_asset_file_path)[1]).name
        source_video_path = INPUT_DIR / "source.mp4"
        download_from_s3(input_s3_asset_file_path, source_video_path)

        # Step 3: Download control signal (optional)
        control_video_path = None
        if control_path:
            logger.info("=" * 80)
            logger.info("Step 3: Downloading control signal video")
            logger.info("=" * 80)

            # control_path is a relative path within the asset in the same bucket
            # Construct full S3 URI from the input file's bucket and asset ID
            input_bucket, _ = parse_s3_uri(input_s3_asset_file_path)
            control_s3_uri = f"s3://{input_bucket}/{asset_id}/{control_path}"
            control_video_path = INPUT_DIR / "control.mp4"
            download_from_s3(control_s3_uri, control_video_path)
        else:
            logger.info("Step 3: No control signal provided, will be computed on-the-fly from source video")

        # Step 4: Run inference
        logger.info("=" * 80)
        logger.info("Step 4: Running inference")
        logger.info("=" * 80)

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        run_inference(
            control_type=control_type,
            prompt=prompt,
            source_video_path=str(source_video_path),
            control_video_path=str(control_video_path) if control_video_path else None,
            control_weight=control_weight,
            output_dir=str(OUTPUT_DIR),
            hf_home=hf_home,
            hf_token=hf_token,
            disable_guardrails=disable_guardrails,
        )

        # Step 5: Find output video
        logger.info("=" * 80)
        logger.info("Step 5: Finding output video")
        logger.info("=" * 80)

        output_video = find_output_video(OUTPUT_DIR)
        if not output_video:
            raise RuntimeError("No output video generated")

        logger.info(f"Output video: {output_video}")

        # Step 6: Determine output S3 key and upload
        logger.info("=" * 80)
        logger.info("Step 6: Uploading output to S3")
        logger.info("=" * 80)

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_bucket = parse_s3_uri(output_s3_asset_files_path)[0]

        # Get the base output path (strip s3://bucket/ prefix)
        _, output_base = parse_s3_uri(output_s3_asset_files_path)
        output_base = output_base.rstrip("/") + "/"

        # Preserve relative path and input filename from the input file
        relative_subdir = compute_relative_subdir(input_s3_asset_file_path, asset_id)
        input_filename_stem = Path(parse_s3_uri(input_s3_asset_file_path)[1]).stem

        output_filename = f"{input_filename_stem}_CosmosTransfer_{control_type}_{timestamp}.mp4"
        output_key = f"{output_base}{relative_subdir}{output_filename}"
        output_s3_uri = f"s3://{output_bucket}/{output_key}"

        # Upload video
        logger.info(f"Uploading video to: {output_s3_uri}")
        logger.info(f"Video file size: {output_video.stat().st_size} bytes")
        sys.stdout.flush()
        try:
            upload_to_s3(output_video, output_s3_uri)
            logger.info("Video uploaded successfully")
            sys.stdout.flush()
        except Exception as upload_err:
            logger.error(f"Failed to upload video: {upload_err}")
            import traceback
            logger.error(traceback.format_exc())
            sys.stdout.flush()
            raise

        # Step 7: Backup HF cache to S3 (non-fatal)
        logger.info("=" * 80)
        logger.info("Step 7: Backing up HF cache to S3")
        logger.info("=" * 80)
        try:
            from model_manager import backup_cache_to_s3
            backup_cache_to_s3(hf_home=hf_home, s3_bucket=s3_model_bucket)
        except Exception as backup_err:
            logger.warning(f"Failed to backup HF cache to S3 (non-fatal): {backup_err}")

        # Done -- container exits with code 0, SFN detects success via RUN_JOB pattern
        elapsed_time = time.time() - start_time

        logger.info("=" * 80)
        logger.info(f"Pipeline completed successfully in {elapsed_time:.1f}s")
        logger.info(f"Output video: {output_s3_uri}")
        logger.info("=" * 80)

    except Exception as e:
        logger.error("=" * 80)
        logger.error("Pipeline failed with error:")
        logger.error(str(e))
        logger.error("=" * 80)
        import traceback
        logger.error(traceback.format_exc())
        sys.stdout.flush()
        sys.stderr.flush()

        # Re-raise to signal container failure (exit code non-zero)
        # SFN RUN_JOB catches this and routes to pipelineEnd Lambda for task failure callback
        raise


if __name__ == "__main__":
    main()
