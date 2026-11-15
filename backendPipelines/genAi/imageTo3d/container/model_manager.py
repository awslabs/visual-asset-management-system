"""
Image to 3D Model Manager

Model weights live in HuggingFace's native HF_HOME cache. Each model key gets its own HF_HOME on
the shared EFS mount and its own S3 backup prefix, so one model's weights never invalidate
another's.

Caching Strategy:
1. Check HF_HOME on EFS for cached weights -> use directly (fast path)
2. If the EFS cache is empty, check the S3 backup -> restore to EFS
3. If there is no S3 backup, let HuggingFace auto-download on first inference
4. After a successful run, back HF_HOME up to S3 for the next job

Every decision the cache logic takes is logged: a slow run is almost always a cache miss, and the
log is the only place that distinguishes a miss from a failed restore.
"""

import logging
import os
import shutil
import subprocess  # nosec B404
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Model checkpoint file extensions that mark a populated HF cache
WEIGHT_EXTENSIONS = (".safetensors", ".bin", ".pth", ".pt", ".ckpt")

# Filesystem types that mean the model cache is genuinely shared between instances. The EFS mount
# helper and a plain NFS mount both present as nfs4; a bind mount of local disk presents as the
# instance's own filesystem, which is what this exists to tell apart.
NETWORK_FILESYSTEMS = ("nfs", "nfs4", "efs")

# A cold HuggingFace download streams its `.incomplete` file straight into the cache, which is an NFS
# mount, and that long write has been cut short by `OSError: [Errno 116] Stale file handle` three times --
# twice with concurrent jobs fetching one repository, and once on a job running alone. The HANDLE is
# invalid, not the file, so reopening succeeds; the partial download is what must not survive, because
# resuming into one is how a neighbouring model's state_dict was corrupted. Each attempt therefore starts
# from a clean slate rather than resuming.
#
# ESTALE is the observed failure; EIO is the same class of transient fault on the same mount and is
# retried for the same reason. Anything else -- no space, permission, a gated repository -- is a real
# answer and is raised immediately, because retrying it only delays the error.
RETRYABLE_FETCH_ERRNOS = (116, 5)
FETCH_ATTEMPTS = 3
FETCH_RETRY_BACKOFF_SECONDS = 5


def _discard_partial_downloads(cache_dir: str, repo: str) -> int:
    """Remove any partial blob left by an interrupted download of ``repo``."""
    removed = 0
    repo_dir = _repo_cache_dir(Path(cache_dir), repo)
    if not repo_dir.is_dir():
        return removed
    for partial in repo_dir.rglob("*.incomplete"):
        try:
            partial.unlink()
            removed += 1
        except OSError as e:
            logger.warning(f"Could not remove the partial download {partial}: {e}")
    return removed


def fetch_repo(repo: str, cache_dir: str, token=None, attempts: int = FETCH_ATTEMPTS) -> str:
    """Download ``repo`` into ``cache_dir``, retrying the shared filesystem's transient faults.

    Returns the local snapshot directory. See RETRYABLE_FETCH_ERRNOS for which failures are retried and
    why a retry discards the partial download rather than resuming it.
    """
    from huggingface_hub import snapshot_download

    last_error = None
    for attempt in range(1, max(int(attempts), 1) + 1):
        try:
            return snapshot_download(repo_id=repo, cache_dir=cache_dir, token=token)
        except OSError as e:
            if getattr(e, "errno", None) not in RETRYABLE_FETCH_ERRNOS:
                raise
            last_error = e
            discarded = _discard_partial_downloads(cache_dir, repo)
            logger.warning(
                f"Fetching {repo} failed on attempt {attempt}/{attempts}: {type(e).__name__}: {e}. "
                f"Discarded {discarded} partial file(s) rather than resuming into them, and retrying.")
            if attempt < attempts:
                time.sleep(FETCH_RETRY_BACKOFF_SECONDS * attempt)

    raise RuntimeError(
        f"Could not fetch '{repo}' into {cache_dir} after {attempts} attempts; the last failure was "
        f"{type(last_error).__name__}: {last_error}. This is the shared model cache filesystem failing "
        f"mid-write rather than a problem with the repository."
    ) from last_error


# rembg keeps its ONNX weights outside the HuggingFace cache, under U2NET_HOME, and fetches a session's
# file from a GitHub release on first use through pooch -- which streams to a ``tmp*`` file beside the
# target and moves it into place once the hash matches. Releases before 2.0.80 keep the file flat under
# that directory; later ones under ``models/<session>/`` and read the flat layout as well. A file smaller
# than this is a truncated copy rather than a weight: the smallest session in use is 176 MB.
MIN_SESSION_WEIGHT_BYTES = 100_000_000


def session_weight_candidates(name: str, fname: str, home: str) -> tuple:
    """Every path a rembg release may keep session ``name``'s weight file at under ``home``."""
    return (os.path.join(home, "models", name, fname), os.path.join(home, fname))


def _complete_weight(path: str) -> bool:
    try:
        return os.path.getsize(path) >= MIN_SESSION_WEIGHT_BYTES
    except OSError:
        return False


def _has_complete_file(candidates) -> bool:
    """Whether one of ``candidates`` is a complete weight file."""
    return any(_complete_weight(path) for path in candidates)


def _session_class(name: str):
    """The installed rembg's session class called ``name``. A name it does not offer is an error rather
    than a fall-through to preprocessing, which would fail the run later and less clearly."""
    import rembg.sessions

    for cls in rembg.sessions.sessions_class:
        if cls.name() == name:
            return cls
    raise RuntimeError(
        f"rembg has no session named {name!r}; the installed release offers "
        f"{', '.join(sorted(cls.name() for cls in rembg.sessions.sessions_class))}")


def _session_weight_dir(cls) -> str:
    """Where the installed rembg release writes ``cls``'s weights, whichever layout it uses."""
    return cls.model_dir() if hasattr(cls, "model_dir") else cls.u2net_home()


def _is_transient_fetch_error(error: BaseException) -> bool:
    """pooch reports a broken connection as a requests error and a hash mismatch as ValueError; both
    are the download failing part-way, like the filesystem faults fetch_repo retries."""
    try:
        from requests.exceptions import RequestException
    except ImportError:  # pragma: no cover - requests arrives with pooch
        RequestException = ()
    if isinstance(error, (RequestException, ValueError)):
        return True
    return isinstance(error, OSError) and getattr(error, "errno", None) in RETRYABLE_FETCH_ERRNOS


def _discard_incomplete_session_weights(candidates) -> int:
    """Remove a truncated file at a weight's final name; rembg would otherwise read it as the weight."""
    removed = 0
    for path in candidates:
        if os.path.isfile(path) and not _complete_weight(path):
            try:
                os.unlink(path)
                removed += 1
                logger.warning(f"Removed the truncated weight file {path}")
            except OSError as e:
                logger.warning(f"Could not remove the truncated weight file {path}: {e}")
    return removed


STALE_DOWNLOAD_SECONDS = 3600


def _discard_stale_session_downloads(directories, max_age_seconds: float = STALE_DOWNLOAD_SECONDS) -> int:
    """Remove the temporary files a job that died left beside a weight. Containers share the cache, so a file
    younger than ``max_age_seconds`` may be another job's download or seed copy in flight and is left alone; a
    download that finishes removes its own temporary file, so an older one has no owner."""
    cutoff = time.time() - max_age_seconds
    removed = 0
    for directory in {os.path.normpath(d) for d in directories if os.path.isdir(d)}:
        for pattern in ("tmp*", "*.partial"):
            for partial in Path(directory).glob(pattern):
                try:
                    if partial.stat().st_mtime > cutoff:
                        continue
                    partial.unlink()
                    removed += 1
                    logger.info(f"Removed the stale partial download {partial}")
                except OSError as e:
                    logger.warning(f"Could not remove the partial download {partial}: {e}")
    return removed


def _seed_session_weight(source: str, target: str) -> None:
    """Copy ``source`` to ``target`` through a sibling of this job's own, so a concurrent reader never opens a
    half-written file at the weight's name and two jobs seeding at once each finish their own copy."""
    directory = os.path.dirname(target)
    os.makedirs(directory, exist_ok=True)
    handle, partial = tempfile.mkstemp(dir=directory, prefix=f"{os.path.basename(target)}.", suffix=".partial")
    os.close(handle)
    try:
        shutil.copyfile(source, partial)
        if not _complete_weight(partial):
            raise OSError(f"the copy of {source} at {partial} is {os.path.getsize(partial)} bytes, not a weight")
        os.replace(partial, target)
    finally:
        if os.path.exists(partial):
            os.unlink(partial)


def fetch_rembg_session(name: str, fname: str, home: str, seed_from: str = None,
                        attempts: int = FETCH_ATTEMPTS) -> str:
    """Put rembg session ``name``'s weight file where the installed release reads it, and return that path.

    ``home`` is what U2NET_HOME names; the installed release decides the layout beneath it. A complete
    file already there costs nothing. A copy in the other layout, or the image's baked copy under
    ``seed_from``, is copied in without touching the network; otherwise the session's own download runs.
    Either way the file arrives under the same retry as fetch_repo, discarding a partial rather than
    resuming into it, and preprocessing then finds it on the shared cache instead of fetching it on every run.
    """
    cls = _session_class(name)
    target = os.path.join(_session_weight_dir(cls), fname)
    if _complete_weight(target):
        logger.info(f"rembg session '{name}' weights cached at {target}")
        return target

    candidates = session_weight_candidates(name, fname, home)
    sources = [path for path in candidates if os.path.normpath(path) != os.path.normpath(target)]
    if seed_from:
        sources.append(os.path.join(seed_from, fname))

    def obtain() -> str:
        for source in sources:
            if _complete_weight(source):
                _seed_session_weight(source, target)
                logger.info(f"Seeded rembg session '{name}' weights at {target} from {source}")
                return target
        # rembg returns an existing file at either layout's name without downloading, so a truncated one
        # has to go before the download is asked for, along with what a job that died left beside it.
        _discard_incomplete_session_weights(candidates + (target,))
        _discard_stale_session_downloads({os.path.dirname(target), home})
        logger.info(f"rembg session '{name}' weights are not cached; fetching them from the rembg release "
                    f"into {target}")
        path = str(cls.download_models())
        if not _complete_weight(path):
            size = os.path.getsize(path) if os.path.exists(path) else 0
            raise ValueError(f"the download at {path} is {size} bytes, not a weight file")
        return path

    last_error = None
    for attempt in range(1, max(int(attempts), 1) + 1):
        try:
            return obtain()
        except Exception as e:
            if not _is_transient_fetch_error(e):
                raise
            last_error = e
            discarded = _discard_incomplete_session_weights(candidates + (target,))
            logger.warning(
                f"Fetching rembg session '{name}' failed on attempt {attempt}/{attempts}: "
                f"{type(e).__name__}: {e}. Discarded {discarded} truncated file(s) rather than resuming "
                f"into them, and retrying.")
            if attempt < attempts:
                time.sleep(FETCH_RETRY_BACKOFF_SECONDS * attempt)

    raise RuntimeError(
        f"Could not fetch rembg session '{name}' into {target} after {attempts} attempts; the last "
        f"failure was {type(last_error).__name__}: {last_error}."
    ) from last_error


def _s3_cache_uri(s3_bucket: str, s3_prefix: str) -> str:
    """The ``s3://`` URI of the per-model HF cache backup prefix."""
    return f"s3://{s3_bucket}/{str(s3_prefix or '').strip('/')}/"


def _repo_cache_dir(hub_dir: Path, repo: str) -> Path:
    """Where the HuggingFace cache keeps one repo: ``hub/models--org--name``."""
    return hub_dir / ("models--" + repo.replace("/", "--"))


def _first_weight_file(root: Path):
    """The first weight file at or below ``root``, or ``None``. Stops at the first hit.

    Walked with os.walk and returned from immediately rather than collected: the previous check built the
    FULL list of every match only to test it for emptiness, and on a network filesystem each entry in that
    list is a round trip.
    """
    for current, _dirs, files in os.walk(root):
        for name in files:
            if name.endswith(WEIGHT_EXTENSIONS):
                return Path(current) / name
    return None


def _has_cached_repo(hub_dir: Path, repo: str) -> bool:
    """Whether ONE repo's snapshot holds a weight file.

    Looks only under that repo's own ``snapshots`` directory, which the HuggingFace cache layout fixes:
    ``hub/models--org--name/snapshots/{revision}/``. Those entries are symlinks into ``blobs``, so the
    walk is over a handful of names rather than over every blob in the cache.
    """
    snapshots = _repo_cache_dir(hub_dir, repo) / "snapshots"
    if not snapshots.is_dir():
        return False
    for revision in snapshots.iterdir():
        if revision.is_dir() and _first_weight_file(revision) is not None:
            return True
    return False


def _has_cached_models(hf_home: str, repos=()) -> bool:
    """Whether HF_HOME holds THIS model's weights.

    Two things this fixes, both measured on live runs.

    **Cost.** The check used to recursively glob the whole cache tree once per weight extension and
    materialise every match to test the list for emptiness. On the EFS mount that reached 477 s of a 606 s
    Step1X-3D run and 243 s of a TRELLIS.2 run -- more than the model's own generation -- against 15 s for
    the smallest cache, all spent establishing that weights already present are present. Scoped to the
    repos this model actually needs, and stopping at the first file, it costs a handful of directory
    reads.

    **Correctness.** The old check answered "does this cache hold ANY weight file", not "are this model's
    weights here". A cache holding one repo of the two a model needs therefore reported a hit, the S3
    restore was skipped, and the missing weights were downloaded during generation instead -- inside the
    model's own stage, where the cost reads as a slow model rather than as a cache miss.

    ``repos`` are the HuggingFace repo ids the model declares. With none given the check falls back to
    asking whether the hub holds anything at all, which is the old question and is all that can be asked
    without knowing the model.
    """
    hf_path = Path(hf_home)
    if not hf_path.exists():
        logger.info(f"HF cache miss: {hf_home} does not exist")
        return False

    hub_dir = hf_path / "hub"
    if not hub_dir.exists():
        logger.info(f"HF cache miss: no hub directory under {hf_home}")
        return False

    started = time.monotonic()
    if repos:
        missing = [repo for repo in repos if not _has_cached_repo(hub_dir, repo)]
        elapsed = time.monotonic() - started
        if missing:
            logger.info(f"HF cache miss in {elapsed:.1f}s: no weights cached for "
                        f"{', '.join(missing)} under {hub_dir}")
            return False
        logger.info(f"HF cache hit in {elapsed:.1f}s: weights present for {', '.join(repos)}")
        return True

    # No declared repos: the most that can be asked is whether the hub holds a weight file anywhere. Still
    # early-exit rather than collecting every match.
    found = _first_weight_file(hub_dir)
    elapsed = time.monotonic() - started
    if found is not None:
        logger.info(f"HF cache hit in {elapsed:.1f}s: found {found.name} under {hub_dir} "
                    f"(no model repos declared, so this does not confirm THIS model's weights)")
        return True
    logger.info(f"HF cache miss in {elapsed:.1f}s: hub directory {hub_dir} holds no "
                f"{WEIGHT_EXTENSIONS} files")
    return False


def _cache_holds(hf_home: str, repos=(), required_files=()) -> bool:
    """Whether HF_HOME holds every repository and every file this run declares.

    A run declaring neither can only be asked the old question, whether anything at all is cached; one
    declaring files alone -- a model with no HuggingFace weights of its own -- is answered by the files.
    """
    if not repos and not required_files:
        return _has_cached_models(hf_home)
    if repos and not _has_cached_models(hf_home, repos):
        return False
    for candidates in required_files:
        if not _has_complete_file(candidates):
            logger.info(f"HF cache miss: none of {', '.join(candidates)} is a complete weight file")
            return False
    return True


def _s3_prefix_exists(s3_bucket: str, s3_prefix: str) -> bool:
    """Whether the S3 backup prefix holds any objects."""
    s3_path = _s3_cache_uri(s3_bucket, s3_prefix)
    try:
        result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
            ["aws", "s3", "ls", s3_path, "--recursive", "--summarize"],
            capture_output=True, text=True, timeout=60
        )  # nosemgrep: dangerous-subprocess-use-audit
        if result.returncode != 0:
            logger.info(f"No S3 cache backup readable at {s3_path}: {result.stderr.strip()}")
            return False
        for line in result.stdout.splitlines():
            if "Total Objects:" in line:
                count = int(line.split(":")[-1].strip())
                logger.info(f"S3 cache backup at {s3_path} holds {count} objects")
                return count > 0
        logger.info(f"S3 cache backup at {s3_path} reported no object summary")
        return False
    except Exception as e:
        logger.warning(f"Failed to check the S3 cache backup at {s3_path}: {e}")
        return False


def _restore_from_s3(hf_home: str, s3_bucket: str, s3_prefix: str) -> bool:
    """Restore HF_HOME from the S3 backup prefix."""
    s3_path = _s3_cache_uri(s3_bucket, s3_prefix)
    hf_path = Path(hf_home)
    hf_path.mkdir(parents=True, exist_ok=True)

    try:
        logger.info(f"Restoring HF cache from S3: {s3_path} -> {hf_home}")
        result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
            ["aws", "s3", "sync", s3_path, str(hf_path), "--quiet"],
            capture_output=True, text=True, timeout=3600
        )  # nosemgrep: dangerous-subprocess-use-audit
        if result.returncode == 0:
            logger.info(f"S3 restore completed from {s3_path}")
            return True
        logger.error(f"S3 restore from {s3_path} failed: {result.stderr.strip()}")
        return False
    except subprocess.TimeoutExpired:
        logger.error(f"S3 restore from {s3_path} timed out after 1 hour")
        return False
    except Exception as e:
        logger.error(f"S3 restore from {s3_path} failed: {e}")
        return False


def _invalidate(hf_home: str, s3_bucket: str, s3_prefix: str) -> None:
    """Clear both the EFS cache and its S3 backup for one model."""
    hf_path = Path(hf_home)
    logger.info(f"Invalidating the HF cache at {hf_home}")
    if hf_path.exists():
        shutil.rmtree(str(hf_path), ignore_errors=True)
        logger.info(f"Deleted the EFS HF cache at {hf_home}")
    else:
        logger.info(f"No EFS HF cache to delete at {hf_home}")

    s3_path = _s3_cache_uri(s3_bucket, s3_prefix)
    try:
        subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
            ["aws", "s3", "rm", s3_path, "--recursive", "--quiet"],
            capture_output=True, text=True, timeout=600
        )  # nosemgrep: dangerous-subprocess-use-audit
        logger.info(f"Deleted the S3 HF cache backup at {s3_path}")
    except Exception as e:
        logger.warning(f"Failed to clear the S3 cache backup at {s3_path}: {e}")


def ensure_models_cached(hf_home: str, s3_bucket: str, s3_prefix: str,
                         invalidate: bool = False, repos=(), required_files=()) -> None:
    """Ensure HF_HOME holds this run's weights, restoring from S3 when it does not.

    Args:
        hf_home: Per-model HF_HOME directory on the shared EFS mount
        s3_bucket: Model cache bucket
        s3_prefix: Per-model key prefix within that bucket
        invalidate: Clear the EFS cache and its S3 backup before checking
        repos: The HuggingFace repo ids this model needs, so the check asks whether THOSE are
            cached rather than whether the cache holds anything at all
        required_files: Weights kept under HF_HOME outside the HuggingFace layout, such as a rembg
            session's ONNX file, each as the candidate paths a complete copy may sit at
    """
    if not s3_bucket:
        raise ValueError("A model cache bucket is required (S3_MODEL_BUCKET)")
    if not s3_prefix:
        raise ValueError("A model cache prefix is required (S3_MODEL_PREFIX)")

    logger.info(f"Model cache: hf_home={hf_home}, s3={_s3_cache_uri(s3_bucket, s3_prefix)}")
    warn_if_cache_is_not_shared()

    if invalidate:
        _invalidate(hf_home, s3_bucket, s3_prefix)

    if _cache_holds(hf_home, repos, required_files):
        logger.info(f"Using the EFS HF cache at {hf_home} (fast path, no download)")
        return

    logger.info("The EFS cache is missing weights this run needs; checking the S3 cache backup")
    if _s3_prefix_exists(s3_bucket, s3_prefix):
        if _restore_from_s3(hf_home, s3_bucket, s3_prefix):
            if _cache_holds(hf_home, repos, required_files):
                logger.info(f"Restored the HF cache from S3 into {hf_home}")
                return
            logger.warning("The S3 restore completed but did not supply every weight this run needs")
    else:
        logger.info("No S3 cache backup for this model")

    logger.info("No backup holds the missing weights; the model's are downloaded during generation and "
                "a segmentation session's by the cache stage. This first run is slower; later runs use "
                "the EFS cache.")
    Path(hf_home).mkdir(parents=True, exist_ok=True)


def backup_cache_to_s3(hf_home: str, s3_bucket: str, s3_prefix: str, required_files=()) -> None:
    """Back HF_HOME up to its S3 prefix. Best-effort: a failure never fails the job.

    ``aws s3 sync`` only uploads new or changed files, so a warm cache costs a listing. A directory
    holding only weights outside the HuggingFace layout -- a model with none of its own, plus its
    segmentation session's file -- is still worth the upload.
    """
    hf_path = Path(hf_home)
    if not hf_path.exists():
        logger.warning(f"HF_HOME does not exist at {hf_home}; nothing to back up")
        return
    if not _has_cached_models(hf_home) and not any(_has_complete_file(c) for c in required_files):
        logger.warning(f"No weight files under {hf_home}; skipping the S3 backup")
        return

    s3_path = _s3_cache_uri(s3_bucket, s3_prefix)
    try:
        logger.info(f"Backing the HF cache up to S3: {hf_home} -> {s3_path}")
        result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
            ["aws", "s3", "sync", str(hf_path), s3_path, "--quiet"],
            capture_output=True, text=True, timeout=3600
        )  # nosemgrep: dangerous-subprocess-use-audit
        if result.returncode == 0:
            logger.info(f"Backed the HF cache up to {s3_path}")
        else:
            logger.warning(f"The S3 backup to {s3_path} had issues: {result.stderr.strip()}")
    except subprocess.TimeoutExpired:
        logger.warning("The S3 cache backup timed out after 1 hour (non-fatal)")
    except Exception as e:
        logger.warning(f"The S3 cache backup failed (non-fatal): {e}")


def warn_if_cache_is_not_shared(mount: str = None) -> bool:
    """Whether the model-cache path is backed by a NETWORK filesystem. Logs loudly when it is not.

    The cache is a host path bind-mounted into the container, and the difference between a shared network
    filesystem and an empty directory on the instance's own disk is the whole value of the cache: when the
    mount silently failed, every run restored its weights from Amazon S3 into local disk and uploaded them
    again -- 15 s for the smallest model's weights and 461 s for the largest.

    Discriminated by filesystem TYPE, not by the presence of a mount point. A first version of this check
    looked for the path in /proc/mounts and reported every run as correctly mounted, because a Docker bind
    mount appears there whatever it is backed by -- a check that cannot fail is worse than no check.
    """
    mount = mount or os.environ.get("MODEL_CACHE_MOUNT", "/mnt/efs/imageto3d-models")
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
        if mount == point or mount.startswith(point.rstrip("/") + "/"):
            if covering is None or len(point) > len(covering[0]):
                covering = (point, fstype)

    if covering and covering[1] in NETWORK_FILESYSTEMS:
        logger.info(f"The model cache at {mount} is on {covering[1]} ({covering[0]}), so it is shared")
        return True

    seen = "nothing" if covering is None else f"{covering[1]} at {covering[0]}"
    logger.warning(
        f"The model cache at {mount} is NOT on a shared filesystem -- it is {seen}, which is this "
        f"instance's own disk. Every run here restores its weights from S3 rather than reading them from "
        f"the shared cache, and backs them up again afterwards. The instance's mount attempt is logged to "
        f"/var/log/imageto3d-efs-mount.log and copied to the model cache bucket under mount-logs/")
    return False


def resolve_hf_home(model_key: str, base: str = None) -> str:
    """The per-model HF_HOME under the shared EFS mount."""
    mount = base or os.environ.get("MODEL_CACHE_MOUNT", "/mnt/efs/imageto3d-models")
    return os.path.join(mount, "hf_cache", model_key)


def resolve_rembg_home(hf_home: str) -> str:
    """Where rembg keeps its session weights: inside the model's HF_HOME, so the S3 backup and restore
    carry them with the model's own weights."""
    return os.path.join(hf_home, "rembg")
