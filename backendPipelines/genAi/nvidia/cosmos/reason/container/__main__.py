"""
VAMS Cosmos Reason 2 Container Wrapper

Orchestrates the full pipeline:
1. Load pipeline definition
2. Ensure models are cached (HF_HOME on EFS, with S3 backup)
3. Download input file (video or image)
4. Run inference via cosmos-reason2-inference CLI
5. Upload TEXT output (JSON) to S3 at outputS3AssetMetadataPath
6. Send Step Functions task success/failure callback
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
        logger.info("VAMS Cosmos Reason 2 Pipeline Starting")
        logger.info("=" * 80)

        definition = load_pipeline_definition()
        logger.info(f"Pipeline definition loaded: {json.dumps(definition, indent=2)}")

        # Extract required fields
        model_size = definition.get("modelSize", "2B")
        cosmos_prompt = definition.get("cosmosPrompt")
        input_parameters_prompt = definition.get("inputParametersPrompt")
        input_s3_asset_file_path = definition.get("inputS3AssetFilePath")
        output_s3_asset_metadata_path = definition.get("outputS3AssetMetadataPath")
        output_s3_asset_files_path = definition.get("outputS3AssetFilesPath")
        asset_id = definition.get("assetId")

        # Get environment variables
        hf_token = os.environ.get("HF_TOKEN")
        s3_model_bucket = os.environ.get("S3_MODEL_BUCKET")

        # Set HF_HOME for native HuggingFace cache on EFS (shared with predict)
        hf_home = HF_CACHE_BASE
        os.environ["HF_HOME"] = hf_home

        if hf_token:
            os.environ["HF_TOKEN"] = hf_token

        # Check for optional flags from inputParameters
        invalidate_models = False
        try:
            input_params = definition.get("inputParameters", "")
            if input_params:
                params = json.loads(input_params) if isinstance(input_params, str) else input_params
                invalidate_models = str(params.get("INVALIDATE_COSMOS_MODELS", "")).lower() == "true"
                if invalidate_models:
                    logger.info("INVALIDATE_COSMOS_MODELS=true: will clear EFS/S3 cache")
        except Exception:
            pass

        if not s3_model_bucket:
            raise ValueError("S3_MODEL_BUCKET environment variable is required")

        if not input_s3_asset_file_path:
            raise ValueError("inputS3AssetFilePath is required - Reason requires an input file")

        if not asset_id:
            raise ValueError("assetId is required in pipeline definition")

        # Use outputS3AssetFilesPath so the JSON shows up as a file in VAMS
        output_s3_path = output_s3_asset_files_path or output_s3_asset_metadata_path
        if not output_s3_path:
            raise ValueError("outputS3AssetFilesPath or outputS3AssetMetadataPath is required")

        # Determine prompt (cosmosPrompt takes precedence)
        prompt = cosmos_prompt if cosmos_prompt else input_parameters_prompt
        if not prompt:
            prompt = "Caption the video in detail."
            logger.info(f"No prompt provided, using default: {prompt}")

        # Model name mapping
        model_name_map = {
            "2B": "nvidia/Cosmos-Reason2-2B",
            "8B": "nvidia/Cosmos-Reason2-8B",
        }
        model_name = model_name_map.get(model_size, "nvidia/Cosmos-Reason2-2B")

        logger.info(f"Model: {model_name} (size={model_size})")
        logger.info(f"Prompt: {prompt}")
        logger.info(f"Input file: {input_s3_asset_file_path}")
        logger.info(f"Output path: {output_s3_path}")
        logger.info(f"Asset ID: {asset_id}")
        logger.info(f"HF_HOME: {hf_home}")

        # Step 1: Ensure models are cached
        logger.info("=" * 80)
        logger.info("Step 1: Ensuring models are cached")
        logger.info("=" * 80)

        ensure_models_cached(
            hf_home=hf_home,
            s3_bucket=s3_model_bucket,
            invalidate=invalidate_models
        )

        # Step 2: Download input file
        logger.info("=" * 80)
        logger.info("Step 2: Downloading input file")
        logger.info("=" * 80)

        # Extract filename from S3 path
        input_filename = Path(parse_s3_uri(input_s3_asset_file_path)[1]).name
        input_file_path = INPUT_DIR / input_filename

        download_from_s3(input_s3_asset_file_path, input_file_path)

        # Step 3: Run inference
        logger.info("=" * 80)
        logger.info("Step 3: Running inference")
        logger.info("=" * 80)

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        result_text = run_inference(
            model_name=model_name,
            model_size=model_size,
            prompt=prompt,
            input_file_path=str(input_file_path),
            output_dir=str(OUTPUT_DIR),
            hf_home=hf_home,
            hf_token=hf_token,
        )

        # Step 4: Save output to JSON file
        logger.info("=" * 80)
        logger.info("Step 4: Saving output")
        logger.info("=" * 80)

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        input_filename_stem = Path(input_filename).stem
        output_json_filename = f"{input_filename_stem}_CosmosReason_{timestamp}.json"
        output_json_path = OUTPUT_DIR / output_json_filename

        # result_text is a dict with: systemPrompt, userPrompt, result, jobLogs, reasoning (optional)
        output_data = {
            "model": model_name,
            "modelSize": model_size,
            "inputFile": input_filename,
            "timestamp": timestamp,
            "systemPrompt": result_text.get("systemPrompt", ""),
            "userPrompt": result_text.get("userPrompt", ""),
            "result": result_text.get("result", ""),
            "jobLogs": result_text.get("jobLogs", ""),
        }
        if result_text.get("reasoning"):
            output_data["reasoning"] = result_text["reasoning"]

        with open(output_json_path, "w") as f:
            json.dump(output_data, f, indent=2)

        logger.info(f"Output saved to: {output_json_path}")

        # Step 5: Upload output to S3
        logger.info("=" * 80)
        logger.info("Step 5: Uploading output to S3")
        logger.info("=" * 80)

        output_bucket = parse_s3_uri(output_s3_path)[0]
        _, output_base = parse_s3_uri(output_s3_path)
        output_base = output_base.rstrip("/") + "/"

        # Preserve relative path from the input file
        relative_subdir = compute_relative_subdir(input_s3_asset_file_path, asset_id)
        output_key = f"{output_base}{relative_subdir}{output_json_filename}"
        output_s3_uri = f"s3://{output_bucket}/{output_key}"

        logger.info(f"Uploading JSON to: {output_s3_uri}")
        sys.stdout.flush()
        try:
            upload_to_s3(output_json_path, output_s3_uri)
            logger.info("Output uploaded successfully")
            sys.stdout.flush()
        except Exception as upload_err:
            logger.error(f"Failed to upload output: {upload_err}")
            import traceback
            logger.error(traceback.format_exc())
            sys.stdout.flush()
            raise

        # Step 6: Backup HF cache to S3 (non-fatal)
        logger.info("=" * 80)
        logger.info("Step 6: Backing up HF cache to S3")
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
        logger.info(f"Output JSON: {output_s3_uri}")
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
