"""
VAMS Gr00t Fine-Tuning Container Wrapper

Orchestrates the full pipeline:
1. Load pipeline definition from sys.argv[1]
2. Ensure base model cached (EFS -> S3 -> HuggingFace)
3. Download asset files from S3 (excluding gr00tOutput_* folders)
4. Resolve config: gr00t_config.json (1st) > asset metadata (2nd) > inputParameters (3rd) > defaults
5. Run fine-tuning via gr00t FinetuneWorkflow
6. Upload checkpoint outputs to S3

Container exits with code 0 on success or non-zero on failure.
SFN task callbacks handled by pipelineEnd Lambda.
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

from inference import run_training
from model_manager import ensure_models_cached, backup_cache_to_s3

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

INPUT_DIR = Path("/tmp/input")
OUTPUT_DIR = Path("/tmp/output")
HF_CACHE_BASE = "/mnt/efs/gr00t-models/hf_cache"

# Default training config values
DEFAULTS = {
    "datasetPath": "dataset",
    "dataConfig": "so100_dualcam",
    "baseModelPath": "nvidia/GR00T-N1.5-3B",
    "maxSteps": 6000,
    "batchSize": 32,
    "learningRate": "1e-4",
    "weightDecay": "1e-5",
    "warmupRatio": "0.05",
    "saveSteps": 2000,
    "numGpus": 1,
    "loraRank": 0,
    "loraAlpha": 16,
    "loraDropout": "0.1",
    "tuneLlm": "false",
    "tuneVisual": "false",
    "tuneProjector": "true",
    "tuneDiffusionModel": "true",
    "embodimentTag": "new_embodiment",
    "videoBackend": "torchvision_av",
}


def load_pipeline_definition() -> Dict:
    """Load pipeline definition from command line argument or environment variable."""
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

    try:
        definition_path = Path(definition_source)
        if definition_path.exists():
            with open(definition_path, "r") as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load as file: {e}")

    raise ValueError(f"Could not parse pipeline definition as JSON or load from file: {definition_source}")


def parse_s3_uri(s3_uri: str) -> Tuple[str, str]:
    """Parse S3 URI into bucket and key."""
    parsed = urlparse(s3_uri)
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    return bucket, key


def download_asset_from_s3(s3_asset_path: str, local_dir: Path) -> None:
    """
    Download asset files from S3, excluding previous gr00tOutput_* folders.
    Only excludes locally -- never deletes from S3.
    """
    local_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Downloading asset from S3: {s3_asset_path} -> {local_dir}")
    logger.info("Excluding gr00tOutput_* folders from download")

    result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
        [
            "aws", "s3", "sync",
            s3_asset_path, str(local_dir),
            "--exclude", "gr00tOutput_*/*",
            "--exclude", "gr00tOutput_*",
        ],
        capture_output=True, text=True
    )  # nosemgrep: dangerous-subprocess-use-audit

    if result.returncode != 0:
        raise RuntimeError(f"S3 asset download failed: {result.stderr}")

    logger.info(f"Asset downloaded to {local_dir}")


def upload_output_to_s3(local_dir: Path, s3_output_path: str, output_folder_name: str) -> str:
    """Upload training output folder to S3."""
    s3_dest = f"{s3_output_path.rstrip('/')}/{output_folder_name}/"

    logger.info(f"Uploading output to S3: {local_dir} -> {s3_dest}")

    result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
        ["aws", "s3", "sync", str(local_dir), s3_dest],
        capture_output=True, text=True
    )  # nosemgrep: dangerous-subprocess-use-audit

    if result.returncode != 0:
        raise RuntimeError(f"S3 output upload failed: {result.stderr}")

    logger.info(f"Output uploaded to {s3_dest}")
    return s3_dest


def resolve_config(definition: Dict, asset_dir: Path) -> Dict:
    """
    Resolve training config using 3-tier priority:
    1. gr00t_config.json in asset (highest)
    2. Asset metadata / merged gr00tConfig from Lambda (middle)
    3. Defaults (lowest)

    Returns merged config dict.
    """
    config = dict(DEFAULTS)

    # Apply gr00tConfig from Lambda (already merged: inputParameters < asset metadata)
    groot_config_str = definition.get("gr00tConfig", "{}")
    if groot_config_str:
        try:
            lambda_config = json.loads(groot_config_str) if isinstance(groot_config_str, str) else groot_config_str
            for key, value in lambda_config.items():
                if value is not None and value != "":
                    config[key] = value
            logger.info(f"Applied Lambda gr00tConfig overrides: {list(lambda_config.keys())}")
        except Exception as e:
            logger.warning(f"Failed to parse gr00tConfig from Lambda: {e}")

    # Check for gr00t_config.json in asset (1st priority -- overrides everything)
    config_file = asset_dir / "gr00t_config.json"
    if config_file.exists():
        try:
            with open(config_file, "r") as f:
                file_config = json.load(f)
            for key, value in file_config.items():
                if value is not None:
                    config[key] = value
            logger.info(f"Applied gr00t_config.json overrides: {list(file_config.keys())}")
        except Exception as e:
            logger.warning(f"Failed to parse gr00t_config.json: {e}")

    return config


def find_latest_checkpoint(output_dir: Path) -> Optional[Path]:
    """Find the latest checkpoint directory in the output."""
    checkpoints = sorted(output_dir.glob("checkpoint-*"), key=lambda p: p.name)
    if checkpoints:
        return checkpoints[-1]
    return None


def main():
    """Main pipeline execution."""
    start_time = time.time()

    try:
        logger.info("=" * 80)
        logger.info("VAMS Gr00t Fine-Tuning Pipeline Starting")
        logger.info("=" * 80)

        definition = load_pipeline_definition()
        logger.info(f"Pipeline definition loaded: {json.dumps(definition, indent=2)}")

        # Extract required fields
        input_s3_asset_path = definition.get("inputS3AssetPath")
        output_s3_asset_files_path = definition.get("outputS3AssetFilesPath")
        asset_id = definition.get("assetId")

        # Get environment variables
        hf_token = os.environ.get("HF_TOKEN")
        s3_model_bucket = os.environ.get("S3_MODEL_BUCKET")
        batch_job_id = os.environ.get("AWS_BATCH_JOB_ID", "unknown")

        # Set HF_HOME for native HuggingFace cache on EFS
        hf_home = HF_CACHE_BASE
        os.environ["HF_HOME"] = hf_home

        if hf_token:
            os.environ["HF_TOKEN"] = hf_token

        # Check for model invalidation flag
        invalidate_models = False
        try:
            input_params = definition.get("inputParameters", "")
            if input_params:
                params = json.loads(input_params) if isinstance(input_params, str) else input_params
                invalidate_models = str(params.get("INVALIDATE_GROOT_MODELS", "")).lower() == "true"
                if invalidate_models:
                    logger.info("INVALIDATE_GROOT_MODELS=true: will clear EFS/S3 cache")
        except Exception:
            pass

        if not s3_model_bucket:
            raise ValueError("S3_MODEL_BUCKET environment variable is required")
        if not input_s3_asset_path:
            raise ValueError("inputS3AssetPath is required in pipeline definition")
        if not asset_id:
            raise ValueError("assetId is required in pipeline definition")
        if not output_s3_asset_files_path:
            raise ValueError("outputS3AssetFilesPath is required in pipeline definition")

        logger.info(f"Asset path: {input_s3_asset_path}")
        logger.info(f"Output path: {output_s3_asset_files_path}")
        logger.info(f"Asset ID: {asset_id}")
        logger.info(f"Batch Job ID: {batch_job_id}")
        logger.info(f"HF_HOME: {hf_home}")

        # Step 1: Ensure base model cached
        logger.info("=" * 80)
        logger.info("Step 1: Ensuring base model is cached")
        logger.info("=" * 80)

        ensure_models_cached(
            hf_home=hf_home,
            s3_bucket=s3_model_bucket,
            invalidate=invalidate_models
        )

        # Step 2: Download asset files from S3
        logger.info("=" * 80)
        logger.info("Step 2: Downloading asset files from S3")
        logger.info("=" * 80)

        download_asset_from_s3(input_s3_asset_path, INPUT_DIR)

        # Step 3: Resolve training config (3-tier priority)
        logger.info("=" * 80)
        logger.info("Step 3: Resolving training configuration")
        logger.info("=" * 80)

        config = resolve_config(definition, INPUT_DIR)
        logger.info(f"Resolved config: {json.dumps(config, indent=2)}")

        # Resolve dataset path
        dataset_path = str(INPUT_DIR / config["datasetPath"])
        if not Path(dataset_path).exists():
            raise ValueError(f"Dataset directory not found at {dataset_path}. "
                           f"Expected LeRobot dataset at '{config['datasetPath']}' within asset.")

        logger.info(f"Dataset path resolved: {dataset_path}")

        # Step 4: Run fine-tuning
        logger.info("=" * 80)
        logger.info("Step 4: Running fine-tuning")
        logger.info("=" * 80)

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        run_training(
            config=config,
            dataset_path=dataset_path,
            output_dir=str(OUTPUT_DIR),
            hf_home=hf_home,
            hf_token=hf_token,
        )

        # Step 5: Build output folder name and upload
        logger.info("=" * 80)
        logger.info("Step 5: Uploading training output to S3")
        logger.info("=" * 80)

        # Extract short model name from path (e.g., "nvidia/GR00T-N1.5-3B" -> "N1.5-3B")
        model_path = config.get("baseModelPath", "nvidia/GR00T-N1.5-3B")
        model_short = model_path.split("/")[-1].replace("GR00T-", "") if "/" in model_path else model_path

        timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        job_id_short = batch_job_id.split("-")[0] if "-" in batch_job_id else batch_job_id[:8]
        output_folder_name = f"gr00tOutput_{model_short}_trainingjob_{timestamp}_{job_id_short}"

        logger.info(f"Output folder name: {output_folder_name}")

        s3_dest = upload_output_to_s3(OUTPUT_DIR, output_s3_asset_files_path, output_folder_name)

        # Step 6: Backup HF cache to S3 (non-fatal)
        logger.info("=" * 80)
        logger.info("Step 6: Backing up HF cache to S3")
        logger.info("=" * 80)
        try:
            backup_cache_to_s3(hf_home=hf_home, s3_bucket=s3_model_bucket)
        except Exception as backup_err:
            logger.warning(f"Failed to backup HF cache to S3 (non-fatal): {backup_err}")

        elapsed_time = time.time() - start_time

        logger.info("=" * 80)
        logger.info(f"Pipeline completed successfully in {elapsed_time:.1f}s")
        logger.info(f"Output: {s3_dest}")
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
        raise


if __name__ == "__main__":
    main()
