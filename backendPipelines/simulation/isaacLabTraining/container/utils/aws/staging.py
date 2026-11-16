#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Asset files staged into the container before Isaac Lab starts.

``trainingConfig.stageAssetPaths`` names files and folders of the executing asset that a task reads
from disk: robot and scene USD files with their materials and textures, meshes, terrains. openPipeline
validates the paths, counts what they select and passes the asset root (``rootS3Uri``) with the
validated paths in the job configuration's ``stageAssetFiles`` block. The selection is listed again
here, because the asset can change between that count and the job start, and the same limits apply.

Every file lands at its asset-relative path under ``STAGED_ASSET_FILES_PATH``, so references between
the files resolve locally as they do in the asset, and the run exports that folder to the Isaac Lab
process as ``STAGED_ASSET_FILES_ENV``. The job role can read every asset bucket, so the paths are
re-validated here and every local path is confirmed to stay inside the staging folder.
"""

import itertools
import os
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from boto3.s3.transfer import TransferConfig

# The image creates /tmp/assets for the runtime user (Dockerfile); the container runs one job.
STAGED_ASSET_FILES_PATH = "/tmp/assets"  # nosec B108 - see the comment above
STAGED_ASSET_FILES_ENV = "VAMS_ASSET_FILES_DIR"

# A second copy of the limits in lambda/openPipeline.py, which rejects an oversized selection before
# the Batch job is submitted. lambda/tests/test_isaaclab_stage_assets.py asserts the two agree.
MAX_STAGE_ASSET_PATHS = 50
MAX_STAGE_ASSET_PATH_LENGTH = 1024
MAX_STAGE_ASSET_PATHS_TOTAL_LENGTH = 2048
MAX_STAGED_ASSET_FILES = 10000
MAX_STAGED_ASSET_BYTES = 50 * 1024 ** 3
# The longest file or folder name a Linux file system holds (NAME_MAX), in bytes.
MAX_STAGED_NAME_BYTES = 255

PREVIEW_FILE_MARKER = ".previewFile."

# Free disk kept beyond the staged files, for Isaac Sim's caches, checkpoints, logs and video.
DISK_HEADROOM_BYTES = 2 * 1024 ** 3
# Files downloaded at once, and parallel ranged GETs within one large file.
DOWNLOAD_WORKERS = 16
TRANSFER_CONFIG = TransferConfig(max_concurrency=4)

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")


def _shown(value) -> str:
    """``value`` quoted for a message, cut short so a long value cannot fill it."""
    text = repr(value)
    return text if len(text) <= 120 else text[:117] + "..."


def normalize_stage_asset_path(value) -> str:
    """One validated path relative to the asset root, by the rules openPipeline applies.

    ``""`` is the whole asset, a trailing ``/`` marks a folder, and a bare path names that file or the
    folder of that name.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"stageAssetPaths entries must be non-blank strings (received {_shown(value)})")
    path = value.strip()
    if len(path) > MAX_STAGE_ASSET_PATH_LENGTH:
        raise ValueError(f"stageAssetPaths entries must be at most {MAX_STAGE_ASSET_PATH_LENGTH} characters")
    if _CONTROL_CHARACTERS.search(path) or "\\" in path or _SCHEME.match(path):
        raise ValueError(f"stageAssetPaths entries must be paths within the asset (received {_shown(value)})")
    path = path.lstrip("/")
    if not path:
        return ""
    segments = path.rstrip("/").split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise ValueError(f"stageAssetPaths entries must stay inside the asset (received {_shown(value)})")
    return "/".join(segments) + ("/" if path.endswith("/") else "")


def _is_staged_key(key: str, root_prefix: str) -> bool:
    if key.endswith("/") or len(key) <= len(root_prefix):
        return False
    return PREVIEW_FILE_MARKER not in key.rsplit("/", 1)[-1]


def parse_stage_config(stage_config: dict):
    """``(bucket, root_prefix, paths)`` from a job configuration's ``stageAssetFiles`` block."""
    if not isinstance(stage_config, dict):
        raise ValueError("stageAssetFiles must be a configuration object")
    parsed = urlparse(str(stage_config.get("rootS3Uri") or ""))
    root_prefix = parsed.path.lstrip("/")
    if parsed.scheme != "s3" or not parsed.netloc or not root_prefix.endswith("/"):
        raise ValueError("stageAssetFiles.rootS3Uri must be the asset root, an s3:// prefix ending in '/'")
    paths = stage_config.get("paths")
    if not isinstance(paths, list) or not paths or len(paths) > MAX_STAGE_ASSET_PATHS:
        raise ValueError(f"stageAssetFiles.paths must list 1 to {MAX_STAGE_ASSET_PATHS} paths")
    paths = [normalize_stage_asset_path(p) for p in paths]
    total_length = sum(len(path) for path in paths)
    if total_length > MAX_STAGE_ASSET_PATHS_TOTAL_LENGTH:
        raise ValueError(
            f"stageAssetPaths entries total {total_length} characters; at most "
            f"{MAX_STAGE_ASSET_PATHS_TOTAL_LENGTH} are allowed")
    return parsed.netloc, root_prefix, paths


def list_selected_files(client, bucket: str, root_prefix: str, paths: list) -> dict:
    """``{key: size}`` of the files the paths select, within the file and byte limits.

    The same listing openPipeline counts: a bare path is its exact key, when there is one, plus the
    folder of that name; a selection that names no file fails the run.
    """
    paginator = client.get_paginator("list_objects_v2")
    files = {}
    total_bytes = 0
    for path in paths:
        matched = False
        exact_match = []
        if path and not path.endswith("/"):
            exact = root_prefix + path
            # The exact key, when it exists, is the first key S3 lists under itself as a prefix.
            response = client.list_objects_v2(Bucket=bucket, Prefix=exact, MaxKeys=1)
            first = (response.get("Contents") or [{}])[0]
            if first.get("Key") == exact:
                exact_match.append(first)
            folder = exact + "/"
        else:
            folder = root_prefix + path
        listed = (obj for page in paginator.paginate(Bucket=bucket, Prefix=folder)
                  for obj in page.get("Contents", []))
        for obj in itertools.chain(exact_match, listed):
            key = obj["Key"]
            if not _is_staged_key(key, root_prefix):
                continue
            matched = True
            if key in files:
                continue
            files[key] = int(obj.get("Size") or 0)
            total_bytes += files[key]
            if len(files) > MAX_STAGED_ASSET_FILES:
                raise ValueError(f"stageAssetPaths selects more than {MAX_STAGED_ASSET_FILES} files")
            if total_bytes > MAX_STAGED_ASSET_BYTES:
                raise ValueError(
                    f"stageAssetPaths selects more than {MAX_STAGED_ASSET_BYTES // 1024 ** 3} GiB of files")
        if not matched:
            raise ValueError(f"stageAssetPaths entry {path or '/'!r} names no file in the asset")
    return files


def unstageable_reason(relative: str):
    """Why an asset file at ``relative``, its path under the asset root, cannot be written under the
    staging folder, or ``None`` when it can. The same check openPipeline makes before the job."""
    segments = relative.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        return "its path has a '.', '..' or empty segment"
    if any(len(segment.encode("utf-8")) > MAX_STAGED_NAME_BYTES for segment in segments):
        return f"a name in its path is longer than {MAX_STAGED_NAME_BYTES} bytes"
    return None


def require_stageable(files, root_prefix: str) -> None:
    """Fail before any download when a selected file cannot be written to disk: one
    ``unstageable_reason`` names, or one at the same path as a folder that holds other selected files."""
    relatives = sorted(key[len(root_prefix):] for key in files)
    folders = set()
    for relative in relatives:
        reason = unstageable_reason(relative)
        if reason:
            raise ValueError(f"stageAssetPaths selects asset file {_shown(relative)}, which cannot be "
                             f"staged: {reason}")
        parts = relative.split("/")
        folders.update("/".join(parts[:i]) for i in range(1, len(parts)))
    for relative in relatives:
        if relative in folders:
            raise ValueError(f"stageAssetPaths selects asset file {_shown(relative)}, which cannot be "
                             f"staged: a folder of other selected files has the same path")


def local_path_for(key: str, root_prefix: str, local_root: str) -> str:
    """Where an asset file lands: its asset-relative path under ``local_root``.

    S3 keys are opaque, so one can hold ``.``, ``..`` or empty segments that would resolve outside
    the staging folder; such a key fails the run rather than being written elsewhere or skipped.
    """
    relative = key[len(root_prefix):]
    segments = relative.split("/")
    if not relative or any(segment in ("", ".", "..") for segment in segments):
        raise ValueError(f"Asset file {_shown(relative)} cannot be staged: its path leaves the staging folder")
    local = os.path.join(local_root, *segments)
    root = os.path.realpath(local_root)
    if os.path.commonpath([root, os.path.realpath(local)]) != root:
        raise ValueError(f"Asset file {_shown(relative)} cannot be staged: its path leaves the staging folder")
    return local


def _existing_parent(path: str) -> str:
    while path and not os.path.exists(path):
        path = os.path.dirname(path)
    return path or "/"


def require_free_disk(local_root: str, total_bytes: int) -> None:
    """Fail before downloading when the staged files and the headroom do not fit on the disk."""
    free = shutil.disk_usage(_existing_parent(local_root)).free
    if total_bytes + DISK_HEADROOM_BYTES > free:
        raise RuntimeError(
            f"stageAssetPaths selects {total_bytes} bytes, but {local_root} has {free} bytes free "
            f"and {DISK_HEADROOM_BYTES} are kept for the run")


def stage_asset_files(s3, stage_config: dict, local_root: str = STAGED_ASSET_FILES_PATH) -> dict:
    """Download the selected asset files under ``local_root``; returns what was staged.

    ``s3`` is the job's ``S3Client``; the asset bucket's requests use the client for its Region.
    """
    started = time.monotonic()
    bucket, root_prefix, paths = parse_stage_config(stage_config)
    client = s3.client_for_bucket(bucket)
    files = list_selected_files(client, bucket, root_prefix, paths)
    require_stageable(files, root_prefix)
    total_bytes = sum(files.values())
    targets = {key: local_path_for(key, root_prefix, local_root) for key in files}
    require_free_disk(local_root, total_bytes)

    def download(key):
        target = targets[key]
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            client.download_file(bucket, key, target, Config=TRANSFER_CONFIG)
        except Exception as e:
            relative = key[len(root_prefix):]
            raise RuntimeError(f"Could not stage asset file {_shown(relative)}: {e}") from e

    os.makedirs(local_root, exist_ok=True)
    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
        futures = [pool.submit(download, key) for key in sorted(files)]
        try:
            for future in futures:
                future.result()
        except Exception:
            # The first failure fails the run; downloads not yet started are not begun.
            for future in futures:
                future.cancel()
            raise

    return {
        "path": local_root,
        "paths": paths,
        "files": len(files),
        "bytes": total_bytes,
        "seconds": round(time.monotonic() - started, 1),
    }
