"""
Gr00t Model Manager

Model caching for GR00T-N1.5-3B using HuggingFace's native HF_HOME cache.

Caching Strategy (same pattern as Cosmos v2.5):
1. Check HF_HOME on EFS for cached models -> use directly (fast path)
2. If EFS cache empty, check S3 backup -> restore to EFS
3. If no S3 backup, let HuggingFace auto-download during training
4. After successful training, backup HF_HOME to S3 for next time
"""

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# The file kinds that mean a repository's weights are really present. A snapshot directory exists as
# soon as a download starts, so its presence proves nothing.
WEIGHT_EXTENSIONS = (".safetensors", ".bin", ".pth", ".ckpt")

# Filesystem types that mean the cache is genuinely shared between instances.
NETWORK_FILESYSTEMS = ("nfs", "nfs4", "efs")


def warn_if_cache_is_not_shared(path: str) -> bool:
    """Whether the cache path is backed by a NETWORK filesystem. Logs loudly when it is not.

    The cache is a host path bind-mounted into the container, and the difference between a shared
    network filesystem and an empty directory on the instance's own disk is the whole value of the
    cache. While the EFS mount silently failed, every run restored its weights from Amazon S3 onto
    local disk and uploaded them again afterwards, and nothing in the logs said so.

    Discriminated by filesystem TYPE, not by the presence of a mount point: a Docker bind mount
    appears in /proc/mounts whatever backs it, so looking for the path there reports every run as
    correctly mounted -- a check that cannot fail is worse than no check.
    """
    try:
        with open("/proc/mounts", encoding="utf-8") as handle:
            entries = [line.split() for line in handle]
    except OSError:
        return True  # Not a Linux container; nothing useful to say.

    # The entry governing the path is the longest mount point that is a prefix of it.
    covering = None
    for fields in entries:
        if len(fields) < 3:
            continue
        point, fstype = fields[1], fields[2]
        if path == point or path.startswith(point.rstrip("/") + "/"):
            if covering is None or len(point) > len(covering[0]):
                covering = (point, fstype)

    if covering and covering[1] in NETWORK_FILESYSTEMS:
        logger.info(f"The model cache at {path} is on {covering[1]} ({covering[0]}), so it is shared")
        return True

    seen = "nothing" if covering is None else f"{covering[1]} at {covering[0]}"
    logger.warning(
        f"The model cache at {path} is NOT on a shared filesystem -- {seen}, which is this "
        f"instance's own disk. Every run here restores its weights from S3 and uploads them again, "
        f"and no other instance can reuse them. Check /var/log/*-efs-mount.log on the instance")
    return False

logging.basicConfig(level=logging.INFO)

S3_HF_CACHE_PREFIX = "gr00t/hf_cache"


def _has_cached_models(hf_home: str) -> bool:
    """Check if HF_HOME contains cached model files."""
    hf_path = Path(hf_home)

    if not hf_path.exists():
        return False

    hub_dir = hf_path / "hub"
    if not hub_dir.exists():
        return False

    # Stopped at the FIRST weight file rather than counting them. The previous form,
    # list(hub_dir.rglob(f"*{ext}")), walked the whole cache tree once per extension and built a list
    # of every match only to test it for emptiness -- over a network filesystem, on every run before
    # any GPU work.
    for ext in WEIGHT_EXTENSIONS:
        found = next(hub_dir.rglob(f"*{ext}"), None)
        if found is not None:
            logger.info(f"Found cached weights in the HF cache ({found.name})")
            return True

    return False


def _s3_prefix_exists(s3_bucket: str) -> bool:
    """Check if S3 backup of HF cache exists."""
    s3_path = f"s3://{s3_bucket}/{S3_HF_CACHE_PREFIX}/"

    try:
        result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
            ["aws", "s3", "ls", s3_path, "--recursive", "--summarize"],
            capture_output=True, text=True, timeout=30
        )  # nosemgrep: dangerous-subprocess-use-audit
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
    """Restore HF cache from S3 backup."""
    s3_path = f"s3://{s3_bucket}/{S3_HF_CACHE_PREFIX}/"
    hf_path = Path(hf_home)
    hf_path.mkdir(parents=True, exist_ok=True)

    try:
        logger.info(f"Restoring HF cache from S3: {s3_path} -> {hf_home}")
        result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
            ["aws", "s3", "sync", s3_path, str(hf_path), "--quiet"],
            capture_output=True, text=True, timeout=3600
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

    1. If invalidate=True, clear EFS cache first
    2. Check if HF_HOME has model files -> use them (fast path)
    3. If empty, check S3 for backup -> restore to EFS
    4. If no S3 backup either, do nothing (HF auto-downloads during training)
    """
    warn_if_cache_is_not_shared(hf_home)

    hf_path = Path(hf_home)

    if invalidate:
        import shutil
        logger.info(f"INVALIDATING HF cache at {hf_home}")
        if hf_path.exists():
            shutil.rmtree(str(hf_path))
            logger.info(f"Deleted EFS HF cache: {hf_home}")

        s3_path = f"s3://{s3_bucket}/{S3_HF_CACHE_PREFIX}/"
        try:
            subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
                ["aws", "s3", "rm", s3_path, "--recursive", "--quiet"],
                capture_output=True, text=True, timeout=300
            )  # nosemgrep: dangerous-subprocess-use-audit
            logger.info(f"Deleted S3 HF cache backup: {s3_path}")
        except Exception as e:
            logger.warning(f"Failed to clear S3 cache: {e}")

    if _has_cached_models(hf_home):
        logger.info(f"HF cache found on EFS at {hf_home}, using fast path")
        return

    logger.info("No HF cache on EFS, checking S3 backup...")
    if _s3_prefix_exists(s3_bucket):
        if _restore_from_s3(hf_home, s3_bucket):
            if _has_cached_models(hf_home):
                logger.info("Successfully restored HF cache from S3")
                return
            else:
                logger.warning("S3 restore completed but no model files found")

    logger.info("No cached models found. HuggingFace will download models during training.")
    hf_path.mkdir(parents=True, exist_ok=True)


def backup_cache_to_s3(hf_home: str, s3_bucket: str) -> None:
    """Backup HF_HOME cache to S3 for persistence across container restarts."""
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
            capture_output=True, text=True, timeout=3600
        )  # nosemgrep: dangerous-subprocess-use-audit

        if result.returncode == 0:
            logger.info("Successfully backed up HF cache to S3")
        else:
            logger.warning(f"S3 backup had issues: {result.stderr}")

    except subprocess.TimeoutExpired:
        logger.warning("S3 backup timed out after 1 hour (non-fatal)")
    except Exception as e:
        logger.warning(f"S3 backup failed (non-fatal): {e}")
