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
    .3mf    128 - 136 per zip byte           106 per zip byte before the download, and 26 per
            (20.3 - 20.4 per uncompressed)   declared uncompressed byte after it

The working disk holds the input, the files it references and the output; the output is at most 2.8
times the input (a binary PLY or GLB written as OBJ text), so the disk need is the input plus 3.5 times
the input and the referenced files.

The limits come from the running function (``AWS_LAMBDA_FUNCTION_MEMORY_SIZE`` and the free space of
the working directory), or, on the AWS Batch tier, from the task (``VAMS_TASK_MEMORY_MIB``), never from a
constant, so a change to the function's memory or ephemeral storage moves them with it.
"""

import os
import shutil
import zipfile

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
    # An FBX is converted to GLB by Assimp first and the GLB loaded; Assimp's own peak is in its
    # process, under the same address-space limit.
    '.fbx': 27,
    # A STEP file is text describing exact geometry; its tessellation is far larger than the text.
    # Measured 55 to 67 on the NIST and Adafruit test files of 0.4 to 0.5 MB (smaller files are
    # dominated by the fixed cost of loading Open CASCADE, which BASE_MEMORY_BYTES covers).
    '.step': 70,
    '.stp': 70,
}
# Used for an extension without a measured factor: the largest measured one.
DEFAULT_MEMORY_EXPANSION_FACTOR = max(MEMORY_EXPANSION_FACTORS.values())

# Factors by output format, where a conversion to that format needs clearly more memory per input byte
# than the input's factor above: the COLLADA exporter holds the model as Python objects as well, and
# the USD writer holds the scene as USD data beside the trimesh model. Each is the measured peak per
# input byte for that pair times 1.26, the margin the input factors keep over their own measurement
# (STL: 14 over 11.1), and applies when it exceeds the input's factor by more than 10 percent. FBX and
# STEP are read as a GLB, so they scale with the GLB row. Without this table a 330 MB STL admitted for
# USDA output peaked at 5,307 of 5,308 MB, so the admitted maximum of every output keeps the headroom
# of the others (a peak of about 70 to 85 percent of the function memory at the limit). The PR 1 pairs
# (STL, OBJ, PLY, GLB, GLTF, XYZ to STL, OBJ, PLY, GLB, GLTF) keep their factors.
OUTPUT_EXPANSION_FACTORS = {
    '.obj': {'.usda': 17},
    '.stl': {'.usda': 17, '.usdc': 52},
    '.off': {'.obj': 10, '.usda': 17},
    '.dae': {
        '.3mf': 149, '.dae': 18, '.fbx': 52, '.glb': 52, '.gltf': 52, '.obj': 13, '.off': 29,
        '.ply': 43, '.step': 135, '.stl': 26, '.stp': 135, '.usda': 27, '.usdc': 90, '.usdz': 88,
    },
    '.3mf': {'.obj': 10, '.usda': 17},
    '.usdc': {
        '.fbx': 42, '.glb': 42, '.gltf': 42, '.obj': 13, '.off': 24, '.ply': 41, '.step': 109,
        '.stl': 22, '.stp': 109, '.usdc': 54, '.usdz': 54,
    },
    '.usda': {
        '.fbx': 41, '.glb': 41, '.gltf': 41, '.obj': 13, '.off': 24, '.ply': 41, '.step': 107,
        '.stl': 21, '.stp': 107, '.usda': 17, '.usdc': 54, '.usdz': 54,
    },
    '.usdz': {
        '.fbx': 42, '.glb': 42, '.gltf': 42, '.obj': 13, '.off': 24, '.ply': 41, '.step': 109,
        '.stl': 21, '.stp': 109, '.usdz': 54,
    },
}
# The input factors are the largest peak resident memory above the interpreter and trimesh baseline,
# per byte of input, over the outputs that need no factor of their own, measured on a 0.33 to 1.3
# million triangle sphere through the worker child (`conversion_worker.convert_model`) and rounded up
# by at least 15 percent. The first six are the factors of the formats this pipeline has always read.
# The output table was measured in the built image (no SciPy) on a 163,842-vertex sphere.

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
    """The input is larger than this function can convert.

    ``total_bytes``, ``limit_bytes`` and ``resource`` carry what the message says, so the AWS Batch tier's
    router can choose a job size from them without parsing the text; they are None when not known."""

    def __init__(self, message, total_bytes=None, limit_bytes=None, resource=None):
        super().__init__(message)
        self.total_bytes = total_bytes
        self.limit_bytes = limit_bytes
        self.resource = resource


# The compute tier a run executes on, from the environment the tier's launcher sets: the AWS Batch
# container carries the task's memory, the Lambda runtime its function's.
TIER_LAMBDA = "lambda"
TIER_BATCH = "batch"
TASK_MEMORY_VARIABLE = "VAMS_TASK_MEMORY_MIB"

# The AWS Batch (Fargate) job sizes the tier runs on, as (vCPU, memory MiB): the Fargate pairs the
# deployment offers. The launcher's selector and the CDK construct (both added with the infrastructure
# change) keep the same ladder.
BATCH_TASK_SIZES = ((4, 30 * 1024), (8, 60 * 1024), (16, 120 * 1024))


def task_memory_bytes(environ=None):
    """The AWS Batch task's memory in bytes from ``VAMS_TASK_MEMORY_MIB``, or None when it is not set
    or not a positive number (on Lambda)."""
    environ = os.environ if environ is None else environ
    try:
        mebibytes = int(environ.get(TASK_MEMORY_VARIABLE, ""))
    except (TypeError, ValueError):
        return None
    return mebibytes * MB if mebibytes > 0 else None


def compute_tier(environ=None):
    """``batch`` on an AWS Batch task, else ``lambda``."""
    return TIER_BATCH if task_memory_bytes(environ) else TIER_LAMBDA


def memory_limit_bytes(environ=None):
    """The memory this run may use in bytes: the Batch task's (``VAMS_TASK_MEMORY_MIB``), else the
    function's (``AWS_LAMBDA_FUNCTION_MEMORY_SIZE``, MB); None when neither is set or a positive
    number (a developer's machine)."""
    environ = os.environ if environ is None else environ
    task = task_memory_bytes(environ)
    if task:
        return task
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
    """Peak memory per input byte for converting ``extension`` to ``output_extension``: the input's
    factor, or the output table's entry for the pair when it has one (it is never lower)."""
    extension = (extension or "").lower()
    factor = MEMORY_EXPANSION_FACTORS.get(extension, DEFAULT_MEMORY_EXPANSION_FACTOR)
    by_output = OUTPUT_EXPANSION_FACTORS.get((output_extension or "").lower(), {})
    return max(factor, by_output.get(extension, factor))


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


# Peak resident memory per UNCOMPRESSED byte of a package whose bytes on disk are compressed. A 3MF's
# peak follows the XML it holds, not the zip: measured 20.3 to 20.4 per uncompressed byte (128 to 136
# per zip byte at a 6.3 to 1 ratio, over 161 to 647 MB of zip), plus about 25%. The per-zip-byte
# factors above stay as the estimate before the download, when only the zip's size is known; the
# declared uncompressed size, read from the central directory after the download, is checked with
# these. The COLLADA output's entry is the per-zip-byte pair factor (149) over the input's (106)
# times the input's uncompressed factor, rounded up.
UNCOMPRESSED_EXPANSION_FACTORS = {
    '.3mf': {None: 26, '.dae': 37},
}


def uncompressed_expansion_factor(extension, output_extension=None):
    """Peak memory per declared uncompressed byte for a compressed package, or None for a format
    whose factor is per byte on disk."""
    rows = UNCOMPRESSED_EXPANSION_FACTORS.get((extension or "").lower())
    if not rows:
        return None
    return max(rows[None], rows.get((output_extension or "").lower(), 0))


def declared_uncompressed_bytes(path):
    """Total uncompressed size the zip's central directory declares, or None when the file is not a
    zip (the format's own check reports that). Reads no member data."""
    try:
        with zipfile.ZipFile(path) as archive:
            return sum(info.file_size for info in archive.infolist())
    except (zipfile.BadZipFile, OSError):
        return None


def uncompressed_limit_bytes(extension, memory_bytes, output_extension=None):
    """The declared uncompressed bytes a compressed package may hold on ``memory_bytes`` of memory, or
    None when the format's factor is per byte on disk or the memory is not known."""
    factor = uncompressed_expansion_factor(extension, output_extension)
    if factor is None or not memory_bytes:
        return None
    return max(int((memory_bytes * MEMORY_USABLE_FRACTION - BASE_MEMORY_BYTES) / factor), 0)


def uncompressed_limits_by_task_memory(extension, output_extension=None, sizes=BATCH_TASK_SIZES):
    """``{task memory MiB: uncompressed limit in bytes}`` for each AWS Batch job size, the counterpart of
    ``limits_by_task_memory`` for a package checked by its declared uncompressed size."""
    return {mib: uncompressed_limit_bytes(extension, mib * MB, output_extension) for _, mib in sizes}


def required_memory_bytes_uncompressed(extension, uncompressed_bytes, output_extension=None):
    """The least memory (bytes) whose uncompressed limit admits ``uncompressed_bytes``, rounded up."""
    usable = uncompressed_bytes * uncompressed_expansion_factor(extension, output_extension) + BASE_MEMORY_BYTES
    return int(-(-usable // MEMORY_USABLE_FRACTION))


def check_uncompressed_size(name, extension, uncompressed_bytes, memory_bytes=None, output_extension=None):
    """Raise ``InputTooLargeError`` when a compressed package's declared uncompressed size is over
    what the function's memory can convert. The message has the shape of ``check_size``'s, and the
    error carries the declared size and the limit, so the Lambda tier can escalate it like an input over
    the size check."""
    limit = uncompressed_limit_bytes(extension, memory_bytes, output_extension)
    if limit is None or uncompressed_bytes is None:
        return None
    if uncompressed_bytes > limit:
        target = (f"converting {extension.lower()} to {output_extension.lower()}" if output_extension
                  else f"{extension.lower()} inputs")
        raise InputTooLargeError(
            f"{name} is {_mb(uncompressed_bytes)} uncompressed, over the {_mb(limit)} uncompressed "
            f"limit for {target}. The limit is set by the {MEMORY_RESOURCE}. Reduce the model's size "
            f"and run the conversion again.",
            total_bytes=uncompressed_bytes, limit_bytes=limit, resource=MEMORY_RESOURCE)
    return limit


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
        target = (f"converting {extension.lower()} to {output_extension.lower()}" if output_extension
                  else f"{extension.lower()} inputs")
        hint = (" A conversion to another format may accept it."
                if output_extension and resource == MEMORY_RESOURCE
                and expansion_factor(extension, output_extension) > expansion_factor(extension) else "")
        raise InputTooLargeError(
            f"{name} is {_mb(total)}, over the {_mb(limit)} limit for {target}. The limit is set by "
            f"the {resource}. Reduce the model's size and run the conversion again.{hint}",
            total_bytes=total, limit_bytes=limit, resource=resource)
    return limit


def required_memory_bytes(extension, total_bytes, output_extension=None):
    """The least memory (bytes) whose limit admits ``total_bytes`` of input and referenced files for
    this pair: the inverse of the memory limit in ``limits``, rounded up."""
    usable = total_bytes * expansion_factor(extension, output_extension) + BASE_MEMORY_BYTES
    return int(-(-usable // MEMORY_USABLE_FRACTION))


def limits_by_task_memory(extension, output_extension=None, sizes=BATCH_TASK_SIZES):
    """``{task memory MiB: memory limit in bytes}`` for each AWS Batch job size, so the router can
    pick the smallest size that admits an input (and name the largest size's limit) without holding
    the factors."""
    return {mib: limits(extension, mib * MB, None, output_extension)[0] for _, mib in sizes}


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


# Peak resident memory per triangle of a USD scene read and written as GLB, measured on the 120 GiB
# Fargate task on 24 to 358 million triangles (266 to 279 bytes, flat), rounded up. USD to STL needs
# about 1.45 times that (SCENE_TRIANGLE_FACTOR_BY_OUTPUT), which the worker's address-space limit still
# bounds.
BYTES_PER_SCENE_TRIANGLE = 270


# How much more memory per triangle an output format needs than GLB (the figure above), where measured.
# Other outputs use the GLB figure.
SCENE_TRIANGLE_FACTOR_BY_OUTPUT = {".stl": 1.45}


def scene_triangle_budget(memory_bytes, floor, output_extension=None):
    """The triangle budget of a scene walk (a USD, STEP or FBX scene) on a task with ``memory_bytes``:
    the memory a conversion may use, less the base, divided by ``BYTES_PER_SCENE_TRIANGLE`` (times the
    output format's factor), never below ``floor`` (the budget the Lambda tier has always had). None when
    the memory is not known."""
    if not memory_bytes:
        return None
    usable = memory_bytes * MEMORY_USABLE_FRACTION - BASE_MEMORY_BYTES
    factor = SCENE_TRIANGLE_FACTOR_BY_OUTPUT.get((output_extension or "").lower(), 1.0)
    return max(int(usable / (BYTES_PER_SCENE_TRIANGLE * factor)), int(floor))


def triangle_budget(environ=None, floor=30_000_000, output_extension=None):
    """The triangle budget this run's scene walks use, or None to keep the constant one. The constant
    (30 million) is what the Lambda tier has always used, and at 10,240 MB the memory-derived budget
    would be 34.6 million, so the Lambda tier keeps the constant; a Batch task derives the budget from
    its own memory."""
    return scene_triangle_budget(task_memory_bytes(environ), floor, output_extension)
