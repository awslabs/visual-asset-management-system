# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Working-directory placement for the two runtimes this package is built into.

The Fargate image runs from ``WORKDIR /app`` with a pre-created, user-owned ``/app/tmp`` and resolves
its work directories relative to the cwd. AWS Lambda mounts everything read-only except ``/tmp`` and
identifies itself through ``LAMBDA_TASK_ROOT``; every directory the code writes is placed under ``/tmp``
there, including the caches PyVista, VTK and matplotlib keep under ``HOME`` / ``MPLCONFIGDIR``, which
``/tmp`` does not carry at a cold start.
"""

import os
import shutil
import tempfile

LAMBDA_WORK_ROOT = "/tmp"  # nosec B108 - the only writable path in the Lambda execution environment

# Environment variables the Lambda image points under the work root; each is created at cold start.
_RUNTIME_DIR_VARS = ("HOME", "MPLCONFIGDIR", "XDG_RUNTIME_DIR", "XDG_CACHE_HOME")


def is_lambda() -> bool:
    return bool(os.environ.get("LAMBDA_TASK_ROOT"))


def work_root() -> str:
    """The directory work directories are created under: ``/tmp`` in Lambda, the cwd (``""``) otherwise."""
    return LAMBDA_WORK_ROOT if is_lambda() else ""


def ensure_runtime_dirs() -> list:
    """Create the cache directories the environment points under the work root. Returns the paths created."""
    created = []
    root = work_root()
    if not root:
        return created
    for var in _RUNTIME_DIR_VARS:
        path = os.environ.get(var, "")
        if not path or not (path == root or path.startswith(root + "/")):
            continue
        if not os.path.isdir(path):
            os.makedirs(path, exist_ok=True)
            created.append(path)
    return created


def make_invocation_dir(prefix: str = "preview_pipeline_") -> str:
    """A fresh directory under the work root for one invocation. ``/tmp`` survives between warm Lambda
    invocations, so the caller removes it with ``remove_dir`` when the invocation ends."""
    return tempfile.mkdtemp(prefix=prefix, dir=work_root() or None)


def remove_dir(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)
