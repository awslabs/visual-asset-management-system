# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bounds for zip-based inputs (.3mf, .sog, members read out of .usdz), checked from the central
directory before anything is extracted or decompressed."""

import zipfile

from ..resources import free_disk_bytes, OUTPUT_DISK_HEADROOM_BYTES

# Above 64 MiB uncompressed, a member compressed more than this is treated as a zip bomb.
MAX_COMPRESSION_RATIO = 200
MAX_MEMBERS = 100_000


class ArchiveLimitError(ValueError):
    pass


def check_member(info: zipfile.ZipInfo, limit_bytes: int):
    if info.file_size > limit_bytes:
        raise ArchiveLimitError(f"archive member {info.filename!r} is {info.file_size:,} bytes uncompressed, "
                                f"over the {limit_bytes:,}-byte limit")
    if info.file_size > 64 * 1024 ** 2 and info.file_size > MAX_COMPRESSION_RATIO * max(info.compress_size, 1):
        raise ArchiveLimitError(f"archive member {info.filename!r} has an implausible compression ratio")


def check_archive(path: str, work_dir: str, headroom_bytes: int = OUTPUT_DISK_HEADROOM_BYTES) -> int:
    """The archive's total uncompressed size, after checking it fits the free disk of ``work_dir``
    (less the output headroom) and that no member looks like a zip bomb."""
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
    if len(members) > MAX_MEMBERS:
        raise ArchiveLimitError(f"archive holds {len(members):,} members, over the {MAX_MEMBERS:,} limit")
    total = sum(info.file_size for info in members)
    budget = free_disk_bytes(work_dir) - headroom_bytes
    if total > budget:
        raise ArchiveLimitError(f"archive is {total / 1024 ** 3:.2f} GiB uncompressed; "
                                f"{max(budget, 0) / 1024 ** 3:.2f} GiB of disk is available")
    for info in members:
        check_member(info, budget)
    return total
