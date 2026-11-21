#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""The text USD the task's memory can parse, read from the files' first bytes before any parse.

pxr can take several times a text layer's (`#usda`) file size to parse it: up to about 7.3 to 7.6
times for prim-heavy layers, about 1.3 times for array-heavy ones. The container sums the text bytes of every USD file it downloaded, including the text
layers inside a `.usdz`, and refuses an asset whose text needs more memory than the task has.

The check bounds text layers only, not the structure of a binary (crate, `PXR-USDC`) layer. Crate
keeps large arrays (points, splats) cheap, but its memory still grows with the prim count, so a
scene with a very large number of prims can exceed the task's memory in either format.

The memory limit is the smallest of the cgroup limit, the task memory the state machine passes in
`VAMS_TASK_MEMORY_MIB` and physical memory: on AWS Fargate the container's cgroup carries no limit
and physical memory is the micro-VM's, larger than the task. Kept free of boto3/pxr imports so it
can be tested in isolation.
"""
import os
import zipfile
from typing import Mapping, Optional

CGROUP_ROOT = "/sys/fs/cgroup"
TASK_MEMORY_ENV = "VAMS_TASK_MEMORY_MIB"

# Memory a text layer costs per byte of file (measured at 7.6), and the memory kept back for the
# interpreter, pxr and the other work of the run.
TEXT_USD_MEMORY_FACTOR = 8
MEMORY_HEADROOM_BYTES = 512 * 1024 ** 2
# Used when no limit can be read (outside a container); the job definition's memory.
FALLBACK_MEMORY_BYTES = 4096 * 1024 ** 2

TEXT_MAGIC = b"#usda"
LAYER_EXTENSIONS = (".usd", ".usda", ".usdc")
USDZ_EXTENSION = ".usdz"


def _read(path: str) -> Optional[str]:
    try:
        with open(path, "r") as handle:
            return handle.read().strip()
    except OSError:
        return None


def _cgroup_memory_limit(cgroup_root: str) -> Optional[int]:
    """cgroup v2 `memory.max`, else v1 `memory.limit_in_bytes`; None when neither sets a limit."""
    for path in (os.path.join(cgroup_root, "memory.max"),
                 os.path.join(cgroup_root, "memory", "memory.limit_in_bytes")):
        value = _read(path)
        if not value or value == "max":
            continue
        try:
            limit = int(value)
        except ValueError:
            continue
        # cgroup v1 reports "unlimited" as a value near 2**63.
        if 0 < limit < 2 ** 60:
            return limit
    return None


def task_memory_bytes(environ: Mapping[str, str]) -> Optional[int]:
    """The task memory in `VAMS_TASK_MEMORY_MIB`; None when unset or not a positive whole number."""
    value = (environ.get(TASK_MEMORY_ENV) or "").strip()
    if not value.isdecimal() or int(value) <= 0:
        return None
    return int(value) * 1024 ** 2


def memory_limit_bytes(environ: Mapping[str, str], cgroup_root: str = CGROUP_ROOT) -> int:
    """The smallest of the cgroup limit, the task memory and physical memory, each when readable."""
    physical = None
    try:
        physical = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        pass
    bounds = [b for b in (_cgroup_memory_limit(cgroup_root), task_memory_bytes(environ), physical) if b]
    return int(min(bounds)) if bounds else FALLBACK_MEMORY_BYTES


def text_usd_budget_bytes(memory_bytes: int) -> int:
    """The text USD bytes a task with `memory_bytes` can parse: 192 MiB at 2 GiB, 448 MiB at 4 GiB."""
    return max(0, (memory_bytes - MEMORY_HEADROOM_BYTES) // TEXT_USD_MEMORY_FACTOR)


def _is_layer(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in LAYER_EXTENSIONS


def text_bytes(path: str) -> int:
    """The bytes of text USD in the file: its size for a text layer, the sizes of its text layer
    members for a `.usdz`, else 0. A crate layer, or a file that is not USD, counts as 0; a
    malformed one is reported when pxr reads it."""
    name = path.lower()
    if name.endswith(USDZ_EXTENSION):
        try:
            with zipfile.ZipFile(path) as package:
                total = 0
                for member in package.infolist():
                    if _is_layer(member.filename):
                        with package.open(member) as layer:
                            if layer.read(len(TEXT_MAGIC)) == TEXT_MAGIC:
                                total += member.file_size
                return total
        except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError):
            return 0
    if not _is_layer(name):
        return 0
    try:
        with open(path, "rb") as layer:
            return os.path.getsize(path) if layer.read(len(TEXT_MAGIC)) == TEXT_MAGIC else 0
    except OSError:
        return 0
