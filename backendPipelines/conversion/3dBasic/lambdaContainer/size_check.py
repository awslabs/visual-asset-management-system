# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Refuse an input that cannot be converted in this function's memory or working disk, before any of
it is downloaded.

A model file is much smaller than the memory its conversion needs: the loader holds the parsed arrays,
the exporter holds a second copy in the output's layout, and a text format is parsed through
intermediate Python objects. The factors below are the peak resident memory (``VmHWM`` of a child
interpreter that loaded the input and exported it, less an interpreter that only imported trimesh)
divided by the input's size, measured with trimesh 5.2.1 and numpy 2.5.5 on generated meshes of 82,000
and 328,000 triangles with vertex colours (the two sizes agree within 3%), taking the largest over the
output formats this pipeline writes and adding about 25%:

    input   measured peak / input size       factor used
    .stl    11.3 - 11.6                      14
    .obj    5.1 - 7.5                        9
    .ply    7.0 - 20.1                       25
    .glb    5.5 - 21.8                       27
    .gltf   as .glb, over the glTF file plus its buffers      27
    .xyz    9.8 - 10.4                       13

The working disk holds the input, the files it references and the output; the output is at most 2.8
times the input (a binary PLY or GLB written as OBJ text), so the disk need is the input plus 3.5 times
the input and the referenced files.

The limits come from the running function (``AWS_LAMBDA_FUNCTION_MEMORY_SIZE`` and the free space of
the working directory), never from a constant, so a change to the function's memory or ephemeral
storage moves them with it.
"""

import os
import shutil

MB = 1024 ** 2

# Peak resident memory a conversion needs per byte of input (input plus the files it references).
MEMORY_EXPANSION_FACTORS = {
    '.stl': 14,
    '.obj': 9,
    '.ply': 25,
    '.glb': 27,
    '.gltf': 27,
    '.xyz': 13,
}
# Used for an extension without a measured factor: the largest measured one.
DEFAULT_MEMORY_EXPANSION_FACTOR = max(MEMORY_EXPANSION_FACTORS.values())

# Memory the interpreter, trimesh and the S3 clients hold before any model is loaded (measured at
# about 50 MB for trimesh alone; the rest is the Lambda runtime, boto3 and headroom).
BASE_MEMORY_BYTES = 300 * MB
# The share of the function's memory a conversion may plan to use; the rest is left for the
# runtime's own allocations and the transfer buffers.
MEMORY_USABLE_FRACTION = 0.9

# The output is at most this many times the input's size (measured: a binary PLY or GLB written as
# OBJ text is 2.8 times larger).
OUTPUT_DISK_FACTOR = 3.5
# Working disk kept free beyond the files themselves.
DISK_HEADROOM_BYTES = 64 * MB

MEMORY_RESOURCE = "function memory"
DISK_RESOURCE = "working disk (/tmp)"


class InputTooLargeError(ValueError):
    """The input is larger than this function can convert."""


def memory_limit_bytes(environ=None):
    """The function's memory in bytes from ``AWS_LAMBDA_FUNCTION_MEMORY_SIZE`` (MB), or None when it
    is not set or not a positive number (outside Lambda)."""
    environ = os.environ if environ is None else environ
    try:
        megabytes = int(environ.get("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", ""))
    except ValueError:
        return None
    return megabytes * MB if megabytes > 0 else None


# Virtual address space the parsers reserve beyond what they use: measured on trimesh 5.2.1 with numpy
# 2.5.5, an interpreter that loads and exports a 7 MB PLY holds about 150 MB resident and reaches
# 1.67 GB of address space, because OpenBLAS reserves thread buffers up front (it fails to start at
# an address-space limit of 1 GB). The worker's limit adds this to the function's memory, so it bounds
# a runaway allocation without refusing an input the size check allows.
ADDRESS_SPACE_OVERHEAD_BYTES = 2 * 1024 * MB


def address_space_limit_bytes(environ=None):
    """The worker's ``RLIMIT_AS``: the function's memory plus the parsers' address-space overhead, or
    None when the memory is not known."""
    memory = memory_limit_bytes(environ)
    return None if memory is None else memory + ADDRESS_SPACE_OVERHEAD_BYTES


def free_disk_bytes(path="/tmp"):
    return shutil.disk_usage(path).free


def expansion_factor(extension):
    return MEMORY_EXPANSION_FACTORS.get((extension or "").lower(), DEFAULT_MEMORY_EXPANSION_FACTOR)


def limits(extension, memory_bytes=None, disk_free_bytes=None):
    """``(max_total_bytes, binding_resource)``: the most the input and its referenced files may total
    in bytes, and which resource sets that. A resource that is unknown (None) sets no limit; with
    neither known the limit is None."""
    candidates = []
    if memory_bytes:
        usable = memory_bytes * MEMORY_USABLE_FRACTION - BASE_MEMORY_BYTES
        candidates.append((max(int(usable / expansion_factor(extension)), 0), MEMORY_RESOURCE))
    if disk_free_bytes is not None:
        usable = disk_free_bytes - DISK_HEADROOM_BYTES
        candidates.append((max(int(usable / (1 + OUTPUT_DISK_FACTOR)), 0), DISK_RESOURCE))
    return min(candidates) if candidates else (None, None)


def _mb(value):
    return f"{value / MB:,.1f} MB"


def check_size(name, extension, size_bytes, memory_bytes=None, disk_free_bytes=None, extra_bytes=0):
    """Raise ``InputTooLargeError`` when ``size_bytes`` (plus ``extra_bytes`` already declared for
    referenced files) is over what the function can convert. Returns the limit in bytes (None when no
    limit is known) so the caller can bound the files the input references."""
    limit, resource = limits(extension, memory_bytes, disk_free_bytes)
    if limit is None:
        return None
    total = size_bytes + extra_bytes
    if total > limit:
        raise InputTooLargeError(
            f"{name} is {_mb(total)}, over the {_mb(limit)} limit for {extension.lower()} inputs. "
            f"The limit is set by the {resource}. Reduce the model's size and run the conversion "
            f"again.")
    return limit


def head_size(client, bucket, key, version_id=""):
    """The size in bytes of the object, at ``version_id`` when given, from a HeadObject that reads
    no data."""
    kwargs = {"Bucket": bucket, "Key": key}
    if version_id:
        kwargs["VersionId"] = version_id
    return int(client.head_object(**kwargs)["ContentLength"])
