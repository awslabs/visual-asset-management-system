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
    # The USD family: a text layer expands little; a binary (crate) layer and a package of one are
    # compressed on disk, and the generic `.usd` extension may be either, so it takes the larger.
    '.usd': 46,
    '.usda': 15,
    '.usdc': 46,
    '.usdz': 46,
    '.dae': 18,
    # A 3MF is a zip of XML, so its bytes on disk are a fraction of what is parsed.
    '.3mf': 106,
    '.off': 20,
}
# The COLLADA exporter holds the whole model as Python objects as well, so a conversion to `.dae`
# needs more per input byte than to any other format. Used in place of the factor above when the
# output is `.dae` (and larger).
DAE_OUTPUT_EXPANSION_FACTORS = {
    '.stl': 23,
    '.obj': 12,
    '.ply': 40,
    '.glb': 50,
    '.gltf': 50,
    '.xyz': 13,
    '.usd': 82,
    '.usda': 25,
    '.usdc': 82,
    '.usdz': 82,
    '.dae': 18,
    '.3mf': 114,
    '.off': 27,
}
# Each factor is the largest peak resident memory above the interpreter and trimesh baseline, per byte
# of input, over the output formats this pipeline writes (the COLLADA output separately), measured on
# a 0.33 to 1.3 million triangle sphere through the worker child (`conversion_worker.convert_model`)
# and rounded up by at least 15 percent. The first six are the factors of the formats this pipeline
# has always read.
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

# The largest single file the worker may write is the working disk's free space less DISK_HEADROOM_BYTES,
# never less than this floor. What a conversion legitimately writes cannot be read from the input's
# size: an OBJ export re-encodes every texture as PNG, and a PNG of a photographic texture is many
# times its JPEG source (a 13.9 MB GLB holding one 8192 x 8192 photo needs a 134 MB PNG), so a limit
# that is a multiple of the input refuses real models. The disk check (see limits) already bounds the
# whole output; this limit only stops one runaway write, such as a decompression bomb, from filling
# the working disk and leaving no room for the cleanup, the logs and the invocation after it.
FILE_SIZE_FLOOR_BYTES = 64 * MB


def file_size_limit_bytes(free_disk_bytes):
    """The worker's ``RLIMIT_FSIZE`` for a job whose working directory has ``free_disk_bytes`` free:
    the free space less the headroom, never less than the floor. ``free_disk_bytes`` is required, so
    a caller that cannot measure the disk fails instead of running without the limit."""
    return max(int(free_disk_bytes) - DISK_HEADROOM_BYTES, FILE_SIZE_FLOOR_BYTES)


def address_space_limit_bytes(environ=None):
    """The worker's ``RLIMIT_AS``: the function's memory plus the parsers' address-space overhead, or
    None when the memory is not known."""
    memory = memory_limit_bytes(environ)
    return None if memory is None else memory + ADDRESS_SPACE_OVERHEAD_BYTES


def free_disk_bytes(path="/tmp"):
    return shutil.disk_usage(path).free


def expansion_factor(extension, output_extension=None):
    extension = (extension or "").lower()
    factor = MEMORY_EXPANSION_FACTORS.get(extension, DEFAULT_MEMORY_EXPANSION_FACTOR)
    if (output_extension or "").lower() == '.dae':
        factor = max(factor, DAE_OUTPUT_EXPANSION_FACTORS.get(extension, DEFAULT_MEMORY_EXPANSION_FACTOR))
    return factor


def limits(extension, memory_bytes=None, disk_free_bytes=None, output_extension=None):
    """``(max_total_bytes, binding_resource)``: the most the input and its referenced files may total
    in bytes, and which resource sets that. A resource that is unknown (None) sets no limit; with
    neither known the limit is None."""
    candidates = []
    if memory_bytes:
        usable = memory_bytes * MEMORY_USABLE_FRACTION - BASE_MEMORY_BYTES
        candidates.append((max(int(usable / expansion_factor(extension, output_extension)), 0), MEMORY_RESOURCE))
    if disk_free_bytes is not None:
        usable = disk_free_bytes - DISK_HEADROOM_BYTES
        candidates.append((max(int(usable / (1 + OUTPUT_DISK_FACTOR)), 0), DISK_RESOURCE))
    return min(candidates) if candidates else (None, None)


def _mb(value):
    return f"{value / MB:,.1f} MB"


def check_size(name, extension, size_bytes, memory_bytes=None, disk_free_bytes=None, extra_bytes=0,
               output_extension=None):
    """Raise ``InputTooLargeError`` when ``size_bytes`` (plus ``extra_bytes`` already declared for
    referenced files) is over what the function can convert. Returns the limit in bytes (None when no
    limit is known) so the caller can bound the files the input references."""
    limit, resource = limits(extension, memory_bytes, disk_free_bytes, output_extension)
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
    return head_object_info(client, bucket, key, version_id)[0]


def head_object_info(client, bucket, key, version_id=""):
    """``(size, etag)`` of the object, at ``version_id`` when given, from a HeadObject that reads no
    data. The ETag is "" when the answer carries none."""
    kwargs = {"Bucket": bucket, "Key": key}
    if version_id:
        kwargs["VersionId"] = version_id
    response = client.head_object(**kwargs)
    etag = response.get("ETag", "") if hasattr(response, "get") else ""
    return int(response["ContentLength"]), etag if isinstance(etag, str) else ""


# Peak resident memory per decoded RGBA byte of a texture image, which the factors above do not cover
# because an image's memory follows its pixels, not its file size. Measured with trimesh 5.2.1 as
# VmHWM less an interpreter that only imported trimesh: a glTF with four 4096 x 4096 PNG textures
# (192 MB of files, 268 MB decoded) peaked at 0.87 - 1.09 GB over the glTF/GLB/OBJ/PLY outputs, 4.1
# times its decoded bytes, and a GLB with one 12,000 x 12,000 texture (0.6 MB of file, 576 MB decoded)
# peaked at 1.65 GB as GLB and 2.2 GB as OBJ, 3.8 times. Plus about 25%.
TEXTURE_MEMORY_FACTOR = 5


def texture_budget_bytes(extension, geometry_bytes, memory_bytes=None, output_extension=None):
    """Decoded RGBA bytes of texture images the function can hold beside a model whose input and
    non-image files total ``geometry_bytes``: the memory a conversion may use, less the base and the
    geometry's share, divided by ``TEXTURE_MEMORY_FACTOR``. None when the memory is not known."""
    if not memory_bytes:
        return None
    usable = memory_bytes * MEMORY_USABLE_FRACTION - BASE_MEMORY_BYTES
    usable -= expansion_factor(extension, output_extension) * geometry_bytes
    return max(int(usable / TEXTURE_MEMORY_FACTOR), 0)
