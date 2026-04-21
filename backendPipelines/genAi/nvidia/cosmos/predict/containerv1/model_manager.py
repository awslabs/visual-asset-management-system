"""
Cosmos Predict Model Manager

Implements EFS/S3/HuggingFace model caching cascade for all Cosmos models:
- Cosmos diffusion models (text2world, video2world)
- Cosmos tokenizer (CV8x8x8-720p, ~5GB, shared)
- T5-11B text encoder (~85GB, shared)

Caching Strategy:
1. Check EFS for model + manifest → if match with S3, use EFS (fast path)
2. If EFS stale/missing but S3 has model → sync S3→EFS
3. If neither → download from HuggingFace, save to EFS, backup to S3
"""

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from huggingface_hub import snapshot_download

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Model mappings
MODEL_CONFIGS = {
    "text2world": {
        "7B": "nvidia/Cosmos-Predict1-7B-Text2World",
        "13B": "nvidia/Cosmos-Predict1-13B-Text2World",
    },
    "video2world": {
        "7B": "nvidia/Cosmos-Predict1-7B-Video2World",
        "13B": "nvidia/Cosmos-Predict1-13B-Video2World",
    },
}

SHARED_MODELS = {
    "tokenizer": "nvidia/Cosmos-Tokenize1-CV8x8x8-720p",
    "text_encoder": "google-t5/t5-11b",
    "guardrail_llama": "meta-llama/Llama-Guard-3-8B",
    "guardrail_cosmos": "nvidia/Cosmos-Guardrail1",
}

# Models where the Cosmos framework expects the FULL HuggingFace ID (org/name)
# as the subdirectory under checkpoint_dir. Other models use just the model name.
MODELS_USING_FULL_HF_PATH = {
    "google-t5/t5-11b",           # loaded as checkpoint_dir/google-t5/t5-11b/
    "meta-llama/Llama-Guard-3-8B",  # loaded as checkpoint_dir/meta-llama/Llama-Guard-3-8B/
    "nvidia/Cosmos-Guardrail1",     # loaded as checkpoint_dir/nvidia/Cosmos-Guardrail1/
}

# Important checkpoint file extensions for manifest
CHECKPOINT_EXTENSIONS = [".safetensors", ".bin", ".json", ".model", ".msgpack"]


def hf_model_id_to_dirname(hf_model_id: str) -> str:
    """
    Convert HuggingFace model ID to directory name under checkpoint_dir.

    The Cosmos framework expects some models at the full org/name path
    (e.g., google-t5/t5-11b/) and others at just the model name
    (e.g., Cosmos-Predict1-7B-Text2World/).
    """
    if hf_model_id in MODELS_USING_FULL_HF_PATH:
        return hf_model_id  # Keep full path: "google-t5/t5-11b"
    return hf_model_id.split("/")[-1]  # Strip org: "nvidia/Cosmos-..." → "Cosmos-..."


def get_required_models(model_type: str, model_size: str) -> List[str]:
    """
    Get list of HuggingFace model IDs required for inference.

    Args:
        model_type: "text2world" or "video2world"
        model_size: "7B" or "13B"

    Returns:
        List of HuggingFace model IDs including diffusion model, tokenizer,
        text encoder, and guardrail models.
    """
    if model_type not in MODEL_CONFIGS:
        raise ValueError(f"Invalid model_type: {model_type}. Must be 'text2world' or 'video2world'")

    if model_size not in MODEL_CONFIGS[model_type]:
        raise ValueError(f"Invalid model_size: {model_size}. Must be '7B' or '13B'")

    diffusion_model = MODEL_CONFIGS[model_type][model_size]

    return [
        diffusion_model,
        SHARED_MODELS["tokenizer"],
        SHARED_MODELS["text_encoder"],
        SHARED_MODELS["guardrail_llama"],
        SHARED_MODELS["guardrail_cosmos"],
    ]


def generate_manifest(model_dir: Path) -> Dict[str, int]:
    """
    Generate manifest for a model directory.

    Includes only key checkpoint files (safetensors, bin, json, model, msgpack).

    Args:
        model_dir: Path to model directory

    Returns:
        Dict mapping filename to file size
    """
    manifest = {}

    for ext in CHECKPOINT_EXTENSIONS:
        for file_path in model_dir.rglob(f"*{ext}"):
            if file_path.is_file():
                relative_path = str(file_path.relative_to(model_dir))
                manifest[relative_path] = file_path.stat().st_size

    return manifest


def save_manifest(manifest: Dict[str, int], manifest_path: Path) -> None:
    """Save manifest to JSON file."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    logger.info(f"Saved manifest to {manifest_path}")


def load_manifest(manifest_path: Path) -> Optional[Dict[str, int]]:
    """Load manifest from JSON file. Returns None if file doesn't exist."""
    if not manifest_path.exists():
        return None

    try:
        with open(manifest_path, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load manifest from {manifest_path}: {e}")
        return None


def manifests_match(manifest1: Optional[Dict[str, int]], manifest2: Optional[Dict[str, int]]) -> bool:
    """Check if two manifests match (same files and sizes)."""
    if manifest1 is None or manifest2 is None:
        return False
    return manifest1 == manifest2


def sync_s3_to_efs(s3_path: str, efs_path: Path) -> bool:
    """
    Sync model from S3 to EFS using AWS CLI.

    Args:
        s3_path: S3 path (s3://bucket/prefix/)
        efs_path: Local EFS path

    Returns:
        True if sync succeeded, False otherwise
    """
    try:
        efs_path.mkdir(parents=True, exist_ok=True)

        cmd = [
            "aws", "s3", "sync",
            s3_path,
            str(efs_path),
            "--quiet"
        ]

        logger.info(f"Syncing S3 -> EFS: {s3_path} -> {efs_path}")
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)  # nosemgrep: dangerous-subprocess-use-audit

        if result.returncode == 0:
            logger.info(f"Successfully synced from S3 to EFS")
            return True
        else:
            logger.error(f"S3 sync failed: {result.stderr}")
            return False

    except subprocess.CalledProcessError as e:
        logger.error(f"S3 sync failed: {e.stderr}")
        return False
    except Exception as e:
        logger.error(f"S3 sync failed: {e}")
        return False


def sync_efs_to_s3(efs_path: Path, s3_path: str) -> bool:
    """
    Sync model from EFS to S3 using AWS CLI.

    Args:
        efs_path: Local EFS path
        s3_path: S3 path (s3://bucket/prefix/)

    Returns:
        True if sync succeeded, False otherwise
    """
    try:
        cmd = [
            "aws", "s3", "sync",
            str(efs_path),
            s3_path,
            "--quiet"
        ]

        logger.info(f"Syncing EFS -> S3: {efs_path} -> {s3_path}")
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)  # nosemgrep: dangerous-subprocess-use-audit

        if result.returncode == 0:
            logger.info(f"Successfully synced from EFS to S3")
            return True
        else:
            logger.error(f"S3 sync failed: {result.stderr}")
            return False

    except subprocess.CalledProcessError as e:
        logger.error(f"S3 sync failed: {e.stderr}")
        return False
    except Exception as e:
        logger.error(f"S3 sync failed: {e}")
        return False


def download_from_huggingface(hf_model_id: str, local_dir: Path, hf_token: Optional[str] = None) -> bool:
    """
    Download model from HuggingFace Hub.

    Args:
        hf_model_id: HuggingFace model ID (e.g., "nvidia/Cosmos-Predict1-7B-Text2World")
        local_dir: Local directory to download to
        hf_token: HuggingFace API token (optional, required for gated models)

    Returns:
        True if download succeeded, False otherwise
    """
    try:
        local_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Downloading {hf_model_id} from HuggingFace Hub...")

        snapshot_download(
            repo_id=hf_model_id,
            local_dir=str(local_dir),
            local_dir_use_symlinks=False,
            token=hf_token,
        )

        logger.info(f"Successfully downloaded {hf_model_id}")
        return True

    except Exception as e:
        logger.error(f"Failed to download {hf_model_id}: {e}")
        return False


def ensure_model_available(
    hf_model_id: str,
    efs_base_path: str,
    s3_bucket: str,
    hf_token: Optional[str] = None,
    invalidate: bool = False
) -> str:
    """
    Ensure model is available on EFS using cascade: EFS → S3 → HuggingFace.

    Cascade Logic:
    1. Check EFS for model files → use EFS directly (fast path)
    2. If EFS empty/missing but S3 has model → sync S3→EFS
    3. If neither → download from HuggingFace, save to EFS, backup to S3

    Args:
        hf_model_id: HuggingFace model ID (e.g., "nvidia/Cosmos-Predict1-7B-Text2World")
        efs_base_path: Base path on EFS (e.g., "/mnt/efs/cosmos-models")
        s3_bucket: S3 bucket for model backups
        hf_token: HuggingFace API token (optional)
        invalidate: If True, clear cached model from EFS and S3 before re-downloading

    Returns:
        Local path to model directory on EFS

    Raises:
        RuntimeError: If model cannot be obtained from any source
    """
    model_name = hf_model_id_to_dirname(hf_model_id)
    # Safe name for manifest files (replace / with --)
    manifest_safe_name = model_name.replace("/", "--")

    # Set up paths
    efs_base = Path(efs_base_path)
    efs_model_dir = efs_base / model_name
    efs_manifest_dir = efs_base / "manifests"
    efs_manifest_path = efs_manifest_dir / f"{manifest_safe_name}.json"

    s3_model_path = f"s3://{s3_bucket}/models/{model_name}/"
    s3_manifest_path = f"s3://{s3_bucket}/manifests/{manifest_safe_name}.json"

    logger.info(f"Ensuring model available: {hf_model_id}")
    logger.info(f"  EFS model dir: {efs_model_dir}")
    logger.info(f"  S3 model path: {s3_model_path}")

    # Invalidation: clear EFS and S3 cache to force re-download from HuggingFace
    if invalidate:
        import shutil
        logger.info(f"INVALIDATING model cache for {hf_model_id}")
        if efs_model_dir.exists():
            shutil.rmtree(str(efs_model_dir))
            logger.info(f"  Deleted EFS: {efs_model_dir}")
        if efs_manifest_path.exists():
            efs_manifest_path.unlink()
            logger.info(f"  Deleted EFS manifest: {efs_manifest_path}")
        # Also clear S3 cache
        try:
            cmd = ["aws", "s3", "rm", s3_model_path, "--recursive", "--quiet"]
            subprocess.run(cmd, check=True, capture_output=True)  # nosemgrep: dangerous-subprocess-use-audit
            cmd = ["aws", "s3", "rm", s3_manifest_path, "--quiet"]
            subprocess.run(cmd, check=True, capture_output=True)  # nosemgrep: dangerous-subprocess-use-audit
            logger.info(f"  Deleted S3 cache: {s3_model_path}")
        except Exception as e:
            logger.warning(f"  Failed to clear S3 cache: {e}")

    # Migration: if the model uses a full HF path (org/name) but was previously
    # downloaded to a flat path (just name), move it to the correct location.
    if hf_model_id in MODELS_USING_FULL_HF_PATH:
        flat_name = hf_model_id.split("/")[-1]
        old_flat_dir = efs_base / flat_name
        if old_flat_dir.exists() and not efs_model_dir.exists():
            logger.info(f"Migrating model from old path {old_flat_dir} to {efs_model_dir}")
            efs_model_dir.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.move(str(old_flat_dir), str(efs_model_dir))
            logger.info(f"Migration complete")

    # Step 1: Check if model exists on EFS
    # Fast path: if the model directory exists and contains checkpoint files, use it directly.
    # Only re-sync from S3 if the model dir is missing or empty.
    if efs_model_dir.exists():
        # Check if there are actual model files (not just an empty dir)
        has_model_files = any(
            f.suffix in (".safetensors", ".bin", ".json", ".model", ".msgpack")
            for f in efs_model_dir.rglob("*") if f.is_file()
        )
        if has_model_files:
            logger.info(f"Model found on EFS, using fast path: {efs_model_dir}")
            return str(efs_model_dir)

    # Step 2: Try syncing from S3
    logger.info(f"Attempting to sync from S3...")

    # Check if model exists in S3 by checking for manifest
    s3_manifest_local = Path("/tmp") / f"{manifest_safe_name}_s3_manifest.json"
    s3_has_model = False

    try:
        cmd = ["aws", "s3", "cp", s3_manifest_path, str(s3_manifest_local), "--quiet"]
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)  # nosemgrep: dangerous-subprocess-use-audit
        if result.returncode == 0 and s3_manifest_local.exists():
            s3_has_model = True
            s3_manifest_local.unlink()  # Clean up
    except:
        pass

    if s3_has_model:
        logger.info(f"Model found in S3, syncing to EFS...")

        # Sync model files
        if sync_s3_to_efs(s3_model_path, efs_model_dir):
            # Sync manifest
            efs_manifest_dir.mkdir(parents=True, exist_ok=True)
            try:
                cmd = ["aws", "s3", "cp", s3_manifest_path, str(efs_manifest_path), "--quiet"]
                subprocess.run(cmd, check=True)  # nosemgrep: dangerous-subprocess-use-audit
                logger.info(f"Successfully synced model and manifest from S3")
                return str(efs_model_dir)
            except Exception as e:
                logger.warning(f"Failed to sync manifest from S3: {e}")
                # Generate new manifest from synced model
                manifest = generate_manifest(efs_model_dir)
                save_manifest(manifest, efs_manifest_path)
                return str(efs_model_dir)
        else:
            logger.warning(f"Failed to sync from S3, will download from HuggingFace")

    # Step 3: Download from HuggingFace
    logger.info(f"Downloading from HuggingFace Hub...")

    if not download_from_huggingface(hf_model_id, efs_model_dir, hf_token):
        raise RuntimeError(f"Failed to download model {hf_model_id} from all sources")

    # Generate manifest
    logger.info(f"Generating manifest for {model_name}...")
    manifest = generate_manifest(efs_model_dir)
    save_manifest(manifest, efs_manifest_path)

    # Backup to S3
    logger.info(f"Backing up model to S3...")
    sync_efs_to_s3(efs_model_dir, s3_model_path)

    # Upload manifest to S3
    try:
        cmd = ["aws", "s3", "cp", str(efs_manifest_path), s3_manifest_path, "--quiet"]
        subprocess.run(cmd, check=True)  # nosemgrep: dangerous-subprocess-use-audit
        logger.info(f"Uploaded manifest to S3")
    except Exception as e:
        logger.warning(f"Failed to upload manifest to S3: {e}")

    logger.info(f"Model {hf_model_id} is now available at {efs_model_dir}")
    return str(efs_model_dir)
