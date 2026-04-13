"""
Cosmos Reason 2 Model Manager

Simplified model management using HuggingFace's native HF_HOME
cache mechanism. Models are automatically downloaded by the inference CLI
on first run.

Caching Strategy:
1. Check HF_HOME on EFS for cached models -> use directly (fast path)
2. If EFS cache empty, check S3 backup -> restore to EFS
3. If no S3 backup, let HuggingFace auto-download during inference
4. After successful inference, backup HF_HOME to S3 for next time

Uses the same EFS mount as predict but a different S3 prefix.
"""

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# S3 prefix for HF cache backups (separate from predict)
S3_HF_CACHE_PREFIX = "cosmos-reason2/hf_cache"


def _has_cached_models(hf_home: str) -> bool:
    """
    Check if HF_HOME contains cached model files.

    Looks for .safetensors or .bin files which indicate downloaded models.

    Args:
        hf_home: Path to HF_HOME directory

    Returns:
        True if model files are present
    """
    hf_path = Path(hf_home)

    if not hf_path.exists():
        return False

    # Check for model checkpoint files in the HF cache structure
    # HF cache stores models under hub/models--org--name/snapshots/*/
    hub_dir = hf_path / "hub"
    if not hub_dir.exists():
        return False

    for ext in (".safetensors", ".bin"):
        matches = list(hub_dir.rglob(f"*{ext}"))
        if matches:
            logger.info(f"Found {len(matches)} {ext} files in HF cache")
            return True

    return False


def _s3_prefix_exists(s3_bucket: str) -> bool:
    """
    Check if S3 backup of HF cache exists.

    Args:
        s3_bucket: S3 bucket name

    Returns:
        True if S3 backup has content
    """
    s3_path = f"s3://{s3_bucket}/{S3_HF_CACHE_PREFIX}/"

    try:
        result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
            ["aws", "s3", "ls", s3_path, "--recursive", "--summarize"],
            capture_output=True, text=True, timeout=30
        )  # nosemgrep: dangerous-subprocess-use-audit
        # Check if any objects exist (look for "Total Objects:" line)
        if result.returncode == 0 and "Total Objects:" in result.stdout:
            for line in result.stdout.splitlines():
                if "Total Objects:" in line:
                    count = int(line.split(":")[-1].strip())
                    if count > 0:
                        logger.info(f"S3 backup exists with {count} objects")
                        return True
        return False
    except Exception as e:
        logger.warning(f"Failed to check S3 backup: {e}")
        return False


def _restore_from_s3(hf_home: str, s3_bucket: str) -> bool:
    """
    Restore HF cache from S3 backup.

    Args:
        hf_home: Path to HF_HOME directory
        s3_bucket: S3 bucket name

    Returns:
        True if restore succeeded
    """
    s3_path = f"s3://{s3_bucket}/{S3_HF_CACHE_PREFIX}/"
    hf_path = Path(hf_home)
    hf_path.mkdir(parents=True, exist_ok=True)

    try:
        logger.info(f"Restoring HF cache from S3: {s3_path} -> {hf_home}")
        result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
            ["aws", "s3", "sync", s3_path, str(hf_path), "--quiet"],
            capture_output=True, text=True, timeout=3600  # 1 hour timeout for large models
        )  # nosemgrep: dangerous-subprocess-use-audit

        if result.returncode == 0:
            logger.info("Successfully restored HF cache from S3")
            return True
        else:
            logger.error(f"S3 restore failed: {result.stderr}")
            return False

    except subprocess.TimeoutExpired:
        logger.error("S3 restore timed out after 1 hour")
        return False
    except Exception as e:
        logger.error(f"S3 restore failed: {e}")
        return False


def ensure_models_cached(hf_home: str, s3_bucket: str, invalidate: bool = False) -> None:
    """
    Ensure HF_HOME has model files, restoring from S3 if needed.

    This is the main entry point for model management. The logic:
    1. If invalidate=True, clear EFS cache first
    2. Check if HF_HOME has model files -> use them (fast path)
    3. If empty, check S3 for backup -> restore to EFS
    4. If no S3 backup either, do nothing (HF auto-downloads during inference)

    Args:
        hf_home: Path to HF_HOME directory
        s3_bucket: S3 bucket for cache backups
        invalidate: If True, clear EFS cache before checking
    """
    hf_path = Path(hf_home)

    # Invalidation: clear EFS cache
    if invalidate:
        import shutil
        logger.info(f"INVALIDATING HF cache at {hf_home}")
        if hf_path.exists():
            shutil.rmtree(str(hf_path))
            logger.info(f"Deleted EFS HF cache: {hf_home}")

        # Also clear S3 backup
        s3_path = f"s3://{s3_bucket}/{S3_HF_CACHE_PREFIX}/"
        try:
            subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
                ["aws", "s3", "rm", s3_path, "--recursive", "--quiet"],
                capture_output=True, text=True, timeout=300
            )  # nosemgrep: dangerous-subprocess-use-audit
            logger.info(f"Deleted S3 HF cache backup: {s3_path}")
        except Exception as e:
            logger.warning(f"Failed to clear S3 cache: {e}")

    # Step 1: Check EFS for cached models (fast path)
    if _has_cached_models(hf_home):
        logger.info(f"HF cache found on EFS at {hf_home}, using fast path")
        return

    # Step 2: Try restoring from S3 backup
    logger.info("No HF cache on EFS, checking S3 backup...")
    if _s3_prefix_exists(s3_bucket):
        if _restore_from_s3(hf_home, s3_bucket):
            if _has_cached_models(hf_home):
                logger.info("Successfully restored HF cache from S3")
                return
            else:
                logger.warning("S3 restore completed but no model files found")

    # Step 3: No cached models anywhere -- HF will auto-download during inference
    logger.info("No cached models found. HuggingFace will download models during inference.")
    logger.info("This will be slower on first run but subsequent runs will use the EFS cache.")
    hf_path.mkdir(parents=True, exist_ok=True)


def backup_cache_to_s3(hf_home: str, s3_bucket: str) -> None:
    """
    Backup HF_HOME cache to S3 for persistence across container restarts.

    Called after successful inference to ensure the downloaded models are
    backed up. Uses `aws s3 sync` which only uploads changed/new files.

    Args:
        hf_home: Path to HF_HOME directory
        s3_bucket: S3 bucket for cache backups
    """
    hf_path = Path(hf_home)

    if not hf_path.exists():
        logger.warning(f"HF_HOME does not exist at {hf_home}, nothing to backup")
        return

    if not _has_cached_models(hf_home):
        logger.warning("No model files in HF cache, skipping backup")
        return

    s3_path = f"s3://{s3_bucket}/{S3_HF_CACHE_PREFIX}/"

    try:
        logger.info(f"Backing up HF cache to S3: {hf_home} -> {s3_path}")
        result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
            ["aws", "s3", "sync", str(hf_path), s3_path, "--quiet"],
            capture_output=True, text=True, timeout=3600  # 1 hour timeout
        )  # nosemgrep: dangerous-subprocess-use-audit

        if result.returncode == 0:
            logger.info("Successfully backed up HF cache to S3")
        else:
            logger.warning(f"S3 backup had issues: {result.stderr}")

    except subprocess.TimeoutExpired:
        logger.warning("S3 backup timed out after 1 hour (non-fatal)")
    except Exception as e:
        logger.warning(f"S3 backup failed (non-fatal): {e}")
