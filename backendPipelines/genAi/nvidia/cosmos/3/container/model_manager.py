"""
Cosmos 3 Model Manager

Simplified model management for v2.5 which uses HuggingFace's native HF_HOME
cache mechanism. Models are automatically downloaded by the inference script
on first run.

Caching Strategy:
1. Check HF_HOME on EFS for cached models -> use directly (fast path)
2. If EFS cache empty, check S3 backup -> restore to EFS
3. If no S3 backup, let HuggingFace auto-download during inference
4. After successful inference, backup HF_HOME to S3 for next time

The key difference from v1: no explicit model download step is needed.
Cosmos Predict 2.5 handles model downloads internally via HF_HOME.
"""

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# S3 prefix for HF cache backups
S3_HF_CACHE_PREFIX = "cosmos3/hf_cache"

# The file kinds that mean a repository's weights are really present. A snapshot directory exists as soon
# as a download starts, so its presence proves nothing.
WEIGHT_EXTENSIONS = (".safetensors", ".bin", ".pth", ".ckpt")

# Filesystem types that mean the cache is genuinely shared between instances.
NETWORK_FILESYSTEMS = ("nfs", "nfs4", "efs")

def _repo_cache_dir(hub_dir, repo: str):
    """Where the HuggingFace cache keeps one repo: ``hub/models--org--name``."""
    return hub_dir / ("models--" + repo.replace("/", "--"))


def _has_cached_repo(hub_dir, repo: str) -> bool:
    """Whether ONE repository's snapshot holds a weight file.

    Looks only under that repository's own ``snapshots`` directory, whose layout the HuggingFace cache
    fixes as ``hub/models--org--name/snapshots/{revision}/``. Those entries are symlinks into ``blobs``,
    so the walk covers a handful of names rather than every blob in the cache.
    """
    snapshots = _repo_cache_dir(hub_dir, repo) / "snapshots"
    if not snapshots.is_dir():
        return False
    for revision in snapshots.iterdir():
        if revision.is_dir():
            for _current, _dirs, files in os.walk(revision):
                if any(name.endswith(WEIGHT_EXTENSIONS) for name in files):
                    return True
    return False



def warn_if_cache_is_not_shared(path: str) -> bool:
    """Whether the cache path is backed by a NETWORK filesystem. Logs loudly when it is not.

    The cache is a host path bind-mounted into the container, and the difference between a shared network
    filesystem and an empty directory on the instance's own disk is the whole value of the cache. When the
    mount silently failed on the Image to 3D pipeline, every run restored its weights from Amazon S3 onto
    local disk and uploaded them again afterwards, and nothing in the logs said so.

    Discriminated by filesystem TYPE, not by the presence of a mount point: a Docker bind mount appears
    in /proc/mounts whatever backs it, so looking for the path there reports every run as correctly
    mounted -- a check that cannot fail is worse than no check.
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
        f"The model cache at {path} is NOT on a shared filesystem -- {seen}, which is this instance's "
        f"own disk. Every run here restores its weights from S3 and uploads them again, and no other "
        f"instance can reuse them. Check /var/log/*-efs-mount.log on the instance")
    return False




def _has_cached_models(hf_home: str, repos=()) -> bool:
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

    # Stopped at the FIRST weight file rather than counting them. The previous form,
    # list(hub_dir.rglob(f"*{ext}")), walked the whole cache tree once per extension and built a list of
    # every match only to test it for emptiness -- over a network filesystem, on every run before any GPU
    # work. Measured on the Image to 3D pipeline, which had the same check: 477 seconds of a 606-second
    # run, more than the model's own generation took.
    # The repositories this RUN needs, when the caller knows them. Asking whether ANY weight file is
    # present cannot distinguish one model variant from another, and these pipelines serve several from
    # one cache directory: 2B and 14B, 2B and 8B, Nano and Super. A hit on the wrong variant skips the
    # restore from S3 and lets HuggingFace download the right one during inference instead, where the
    # cost reads as a slow model rather than as a cache miss.
    if repos:
        missing = [repo for repo in repos if not _has_cached_repo(hub_dir, repo)]
        if missing:
            logger.info(f"HF cache miss: no weights for {', '.join(missing)} under {hub_dir}")
            return False
        logger.info(f"HF cache hit: weights present for {', '.join(repos)}")
        return True

    # Without a declared repository all that can be asked is whether anything is cached at all.
    for ext in WEIGHT_EXTENSIONS:
        found = next(hub_dir.rglob(f"*{ext}"), None)
        if found is not None:
            logger.info(f"HF cache hit: {found.name} under {hub_dir} (no repository declared, so this "
                        f"does not confirm THIS run's model)")
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
        ) # nosemgrep: dangerous-subprocess-use-audit
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
        )

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


def ensure_models_cached(hf_home: str, s3_bucket: str, invalidate: bool = False,
                         repos=()) -> None:
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
    warn_if_cache_is_not_shared(hf_home)

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
            )
            logger.info(f"Deleted S3 HF cache backup: {s3_path}")
        except Exception as e:
            logger.warning(f"Failed to clear S3 cache: {e}")

    # Step 1: Check EFS for cached models (fast path)
    if _has_cached_models(hf_home, repos):
        logger.info(f"HF cache found on EFS at {hf_home}, using fast path")
        return

    # Step 2: Try restoring from S3 backup
    logger.info("No HF cache on EFS, checking S3 backup...")
    if _s3_prefix_exists(s3_bucket):
        if _restore_from_s3(hf_home, s3_bucket):
            if _has_cached_models(hf_home, repos):
                logger.info("Successfully restored HF cache from S3")
                return
            else:
                logger.warning("S3 restore completed but no model files found")

    # Step 3: No cached models anywhere -- HF will auto-download during inference
    logger.info("No cached models found. HuggingFace will download models during inference.")
    logger.info("This will be slower on first run but subsequent runs will use the EFS cache.")
    hf_path.mkdir(parents=True, exist_ok=True)


def backup_cache_to_s3(hf_home: str, s3_bucket: str, repos=()) -> None:
    """
    Backup HF_HOME cache to S3 for persistence across container restarts.

    Called after successful inference to ensure the downloaded models are
    backed up. Uses `aws s3 sync` which only uploads changed/new files.

    Args:
        hf_home: Path to HF_HOME directory
        s3_bucket: S3 bucket for cache backups
        repos: The repositories this run needs, matching _has_cached_models. When
            given, the skip-if-nothing-cached guard asks about THIS run's weights
            rather than any weights at all. Empty falls back to "is anything cached".
    """
    hf_path = Path(hf_home)

    if not hf_path.exists():
        logger.warning(f"HF_HOME does not exist at {hf_home}, nothing to backup")
        return

    if not _has_cached_models(hf_home, repos):
        logger.warning("No model files in HF cache, skipping backup")
        return

    s3_path = f"s3://{s3_bucket}/{S3_HF_CACHE_PREFIX}/"

    try:
        logger.info(f"Backing up HF cache to S3: {hf_home} -> {s3_path}")
        result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
           
            ["aws", "s3", "sync", str(hf_path), s3_path, "--quiet"],
            capture_output=True, text=True, timeout=3600  # 1 hour timeout
        )

        if result.returncode == 0:
            logger.info("Successfully backed up HF cache to S3")
        else:
            logger.warning(f"S3 backup had issues: {result.stderr}")

    except subprocess.TimeoutExpired:
        logger.warning("S3 backup timed out after 1 hour (non-fatal)")
    except Exception as e:
        logger.warning(f"S3 backup failed (non-fatal): {e}")
