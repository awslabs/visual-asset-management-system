# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Compute resources of the running task and the render budgets derived from them.

The budgets come from what the container can actually use -- the cgroup memory limit and the CPU
quota -- never from a tier name, so a job runs within its task's limits whichever job definition or
override started it. ``VAMS_COMPUTE_TIER`` (set by the state machine when it picks a tier) is only
logged.

Defaults were measured on the CPU rasteriser (llvmpipe) at 800x600, 36 frames, limited to 4 vCPU and
16 GiB: 5 million points render in about 100 s and 20 million triangles in about 100 s with
``LP_NUM_THREADS=4``, so a 4 vCPU task stays within a few minutes of render time at its budget.
"""

import math
import os
import shutil
from dataclasses import dataclass
from typing import Optional

from .utils.logging import get_logger

logger = get_logger()

CGROUP_ROOT = "/sys/fs/cgroup"

COMPUTE_TIER_ENV = "VAMS_COMPUTE_TIER"
# The values the state machine sets in COMPUTE_TIER_ENV.
STANDARD_TIER = "standard"
LARGE_TIER = "large"

# Per-vCPU render budgets (measured, see the module docstring) and the ceilings above which more
# geometry adds nothing visible to an 800x600 thumbnail.
POINTS_PER_VCPU = 5_000_000
TRIANGLES_PER_VCPU = 5_000_000
SPLATS_PER_VCPU = 1_000_000
MAX_POINTS = 20_000_000
MAX_TRIANGLES = 30_000_000
MAX_SPLATS = 5_000_000
MIN_POINTS = 1_000_000
MIN_TRIANGLES = 1_000_000
MIN_SPLATS = 250_000

# Share of the memory limit the rendered geometry may hold, and the resident bytes one primitive costs
# through load, VTK and the rasteriser (coordinates, colours, normals, cell arrays and their copies).
GEOMETRY_MEMORY_FRACTION = 0.35
BYTES_PER_POINT = 120
BYTES_PER_TRIANGLE = 200
BYTES_PER_SPLAT = 200

# Used when no limit can be read (outside a container).
FALLBACK_MEMORY_BYTES = 16 * 1024 ** 3

# Free disk kept beyond the input for fetched dependencies' slack, the frames and the encoded output.
OUTPUT_DISK_HEADROOM_BYTES = 1024 ** 3

# Advice in the disk-fit refusal. It holds on every tier, since every tier has the same work volume.
DISK_ADVICE = ("The input is larger than the task's work volume; reduce the input size or split it "
               "into smaller files.")


class InsufficientDiskError(Exception):
    """The input does not fit on the work volume."""


@dataclass(frozen=True)
class Budgets:
    memory_bytes: int
    cpus: int
    points: int
    triangles: int
    splats: int

    def describe(self) -> str:
        return (f"memory={self.memory_bytes / 1024 ** 3:.1f} GiB cpus={self.cpus} "
                f"points={self.points:,} triangles={self.triangles:,} splats={self.splats:,}")


def _read(path: str) -> Optional[str]:
    try:
        with open(path, "r") as handle:
            return handle.read().strip()
    except OSError:
        return None


def memory_limit_bytes(cgroup_root: str = CGROUP_ROOT) -> int:
    """The container's memory limit: cgroup v2 ``memory.max``, else v1 ``memory.limit_in_bytes``,
    else physical memory. An unlimited cgroup reads as physical memory."""
    physical = None
    try:
        physical = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        pass
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
        if limit <= 0 or limit >= 2 ** 60:
            continue
        return min(limit, physical) if physical else limit
    return int(physical) if physical else FALLBACK_MEMORY_BYTES


def cpu_count(cgroup_root: str = CGROUP_ROOT) -> int:
    """The CPUs the process may use: the scheduler affinity, capped by the cgroup CPU quota
    (v2 ``cpu.max``, v1 ``cpu.cfs_quota_us``/``cpu.cfs_period_us``). ``os.cpu_count()`` alone reports
    the host's CPUs inside a container limited by quota."""
    try:
        available = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        available = os.cpu_count() or 1
    quota = None
    value = _read(os.path.join(cgroup_root, "cpu.max"))
    if value:
        parts = value.split()
        if len(parts) == 2 and parts[0] != "max":
            try:
                quota = int(parts[0]) / int(parts[1])
            except (ValueError, ZeroDivisionError):
                quota = None
    if quota is None:
        quota_us = _read(os.path.join(cgroup_root, "cpu", "cpu.cfs_quota_us"))
        period_us = _read(os.path.join(cgroup_root, "cpu", "cpu.cfs_period_us"))
        try:
            if quota_us and period_us and int(quota_us) > 0:
                quota = int(quota_us) / int(period_us)
        except (ValueError, ZeroDivisionError):
            quota = None
    if quota is not None and quota > 0:
        available = min(available, max(1, math.ceil(quota - 1e-9)))
    return max(1, int(available))


def _budget(per_vcpu, cpus, bytes_each, memory_bytes, floor, ceiling):
    by_cpu = per_vcpu * cpus
    by_memory = int(memory_bytes * GEOMETRY_MEMORY_FRACTION / bytes_each)
    return int(max(floor, min(ceiling, by_cpu, by_memory)))


def compute_budgets(memory_bytes: Optional[int] = None, cpus: Optional[int] = None) -> Budgets:
    """Render budgets for a task with ``memory_bytes`` of memory and ``cpus`` CPUs (read from the
    running container when omitted)."""
    memory_bytes = int(memory_bytes if memory_bytes is not None else memory_limit_bytes())
    cpus = int(cpus if cpus is not None else cpu_count())
    return Budgets(
        memory_bytes=memory_bytes,
        cpus=cpus,
        points=_budget(POINTS_PER_VCPU, cpus, BYTES_PER_POINT, memory_bytes, MIN_POINTS, MAX_POINTS),
        triangles=_budget(TRIANGLES_PER_VCPU, cpus, BYTES_PER_TRIANGLE, memory_bytes,
                          MIN_TRIANGLES, MAX_TRIANGLES),
        splats=_budget(SPLATS_PER_VCPU, cpus, BYTES_PER_SPLAT, memory_bytes, MIN_SPLATS, MAX_SPLATS),
    )


def compute_tier() -> Optional[str]:
    """The tier the state machine chose, when it set one."""
    value = (os.environ.get(COMPUTE_TIER_ENV) or "").strip()
    return value or None


def memory_advice(tier: Optional[str]) -> str:
    """What a run that ran out of memory on ``tier`` (``compute_tier()``) can do next. Only the
    standard tier has a larger tier to move to; with no tier set (a local test, or a deployment
    without compute tiers) no tier is named, since none may exist."""
    tier = (tier or "").lower()
    if tier == STANDARD_TIER:
        return "Run the large compute tier."
    if tier == LARGE_TIER:
        return "The input exceeds the memory of the largest compute tier; reduce the input size."
    return "Reduce the input size, or use a larger compute size if the deployment offers one."


def configure_rasteriser_threads(cpus: int) -> None:
    """Run the CPU rasteriser on the task's CPUs. llvmpipe otherwise starts one thread per host CPU,
    which inside a CPU quota only adds contention. Set before the first GL context is created; a
    value already in the environment is kept."""
    os.environ.setdefault("LP_NUM_THREADS", str(max(1, int(cpus))))
    os.environ.setdefault("GALLIUM_DRIVER", "llvmpipe")


def free_disk_bytes(path: str) -> int:
    """Free bytes on the volume holding ``path`` (its nearest existing parent)."""
    probe = os.path.abspath(path)
    while not os.path.exists(probe) and os.path.dirname(probe) != probe:
        probe = os.path.dirname(probe)
    return int(shutil.disk_usage(probe).free)


def ensure_disk_space(path: str, need_bytes: int, headroom_bytes: int = OUTPUT_DISK_HEADROOM_BYTES,
                      what: str = "Input file") -> int:
    """Raise InsufficientDiskError when ``need_bytes`` plus the headroom does not fit on the volume
    holding ``path``. Returns the free bytes.

    Every compute tier runs on the same work volume (ephemeral storage is a job-definition property,
    not a per-job override), so the refusal never suggests another tier."""
    free = free_disk_bytes(path)
    if need_bytes + headroom_bytes > free:
        raise InsufficientDiskError(
            f"{what} ({need_bytes / 1024 ** 3:.2f} GiB) does not fit on the work volume: "
            f"{free / 1024 ** 3:.2f} GiB free, {headroom_bytes / 1024 ** 3:.2f} GiB of which is kept "
            f"for the output. {DISK_ADVICE}")
    return free
