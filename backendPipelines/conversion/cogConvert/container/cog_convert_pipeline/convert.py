# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Naming, limits, the GDAL call and the link attributes for the TIF to COG conversion."""

import collections
import json
import os
import re
import shutil
import subprocess  # nosec B404 - the GDAL command line is run with an argument list, never a shell
from typing import Dict, List, Optional, Tuple

from .utils.logging import log

logger = log.get_logger()

# --- the output profile: ONE place to change what the pipeline writes ----------------------------------------------
#
# The map viewer (@geomatico/maplibre-cog-protocol) reads only COGs in EPSG:3857; any other CRS draws a blank layer
# with no error. The conversion therefore reprojects into Web Mercator with GDAL's GoogleMapsCompatible tiling
# scheme. That RESAMPLES the pixels: the output is not a pixel copy of the input. The owner's confirmation of
# this choice is pending, so the CRS and the tiling scheme live only in this block. To write the source's own CRS
# instead, set OUTPUT_CRS to "native", TILING_SCHEME to None and re-read `skip rule` in tiff_header.needs_conversion.
OUTPUT_CRS = "EPSG:3857"
TILING_SCHEME: Optional[str] = "GoogleMapsCompatible"
ALLOWED_COMPRESSION = ("DEFLATE",)
DEFAULT_COMPRESSION = "DEFLATE"
COG_SUFFIX = ".cog.tif"
ATTRIBUTE_GROUP = "cog_link"
TOOL_NAME = "gdal"

# --- limits ---------------------------------------------------------------------------------------------------------
# Refused before any GDAL work. The raw figure is width x height x bands x bytes per sample, uncompressed.
MAX_RAW_BYTES = 256 * 1024 ** 3
MAX_INPUT_BYTES = 100 * 1024 ** 3
MAX_BANDS = 16
# Free space needed beside the downloaded input, as a multiple of the input size. A COG with overviews and a
# DEFLATE predictor is typically 1.3x the input; the COG driver writes a temporary file of that size before the
# final one, and reprojection into Web Mercator can enlarge the pixel grid (a geographic file grows towards the
# poles). 3.5x covers the temporary plus the final file with margin; it is a measured-later figure, so it is a
# constant here and not scattered.
DISK_HEADROOM_FACTOR = 3.5
MIN_FREE_BYTES = 1 * 1024 ** 3

_TAIL_LINES = 80
_TAIL_LINE_CHARS = 2000

_GDAL_ENV_KEYS = ("PATH", "LD_LIBRARY_PATH", "GDAL_DATA", "PROJ_DATA", "HOME", "LANG", "LC_ALL", "TMPDIR")


class ConversionRefused(RuntimeError):
    """The file is outside the limits; the message says which one."""


def derived_output_name(input_name: str) -> str:
    """`a.tif` -> `a.tif.cog.tif`. The suffix is appended to the whole source file name, so `a.tif` and `a.tiff`
    in one folder cannot collide and the source name is the COG name minus the suffix."""
    return input_name + COG_SUFFIX


def relative_subdir(object_key: str, asset_id: str) -> str:
    """The input's folder within the asset, sliced at the threaded assetId segment ('' at the asset root)."""
    if not asset_id:
        return ""
    parts = object_key.split("/")
    if asset_id not in parts:
        logger.warning(f"assetId {asset_id} is not a segment of {object_key}; output goes to the output prefix root")
        return ""
    return "/".join(parts[parts.index(asset_id) + 1:-1])


def join_key(*segments: str) -> str:
    parts = [segment.strip("/") for segment in segments if segment]
    return "/".join(part for part in parts if part)


def asset_relative_path(subdir: str, file_name: str) -> str:
    """The asset-relative file path with a leading slash, as the attribute catalogue names a file."""
    return "/" + join_key(subdir, file_name)


def parse_params(raw) -> Dict[str, str]:
    """The run's conversion settings, checked. Anything outside the allowed set fails the run."""
    params = {}
    if raw:
        params = json.loads(raw) if isinstance(raw, str) else dict(raw)
    compression = str(params.get("compression") or DEFAULT_COMPRESSION).upper()
    if compression not in ALLOWED_COMPRESSION:
        raise ValueError(f"compression must be one of {list(ALLOWED_COMPRESSION)}, not '{compression}'")
    return {"compression": compression}


# --- task sizing ----------------------------------------------------------------------------------------------------

def _read_int(path: str) -> Optional[int]:
    try:
        with open(path, encoding="ascii") as handle:
            text = handle.read().strip()
    except OSError:
        return None
    return int(text) if text.isdigit() and int(text) < 2 ** 60 else None


def task_memory_bytes() -> int:
    """The smallest of the cgroup limit and the task memory the job definition names (VAMS_TASK_MEMORY_MIB); on
    Fargate the cgroup alone is unlimited and physical memory is the micro-VM's, larger than the task."""
    candidates = []
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        value = _read_int(path)
        if value:
            candidates.append(value)
    task = os.environ.get("VAMS_TASK_MEMORY_MIB", "")
    if task.isdigit() and int(task) > 0:
        candidates.append(int(task) * 1024 ** 2)
    try:
        candidates.append(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
    except (ValueError, OSError):
        pass
    return min(candidates) if candidates else 4 * 1024 ** 3


def task_cpus() -> int:
    try:
        cpus = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        cpus = os.cpu_count() or 1
    try:
        with open("/sys/fs/cgroup/cpu.max", encoding="ascii") as handle:
            quota, period = handle.read().split()[:2]
        if quota != "max":
            cpus = min(cpus, max(1, int(int(quota) / int(period))))
    except (OSError, ValueError):
        pass
    return max(1, cpus)


# --- checks before the work -----------------------------------------------------------------------------------------

def check_limits(header, input_bytes: int) -> None:
    """Refuse a file over the pixel, size or band limits, naming the limit."""
    if input_bytes > MAX_INPUT_BYTES:
        raise ConversionRefused(f"The input is {input_bytes / 2**30:.1f} GiB; the limit is {MAX_INPUT_BYTES // 2**30} GiB.")
    if header.band_count > MAX_BANDS:
        raise ConversionRefused(f"The input has {header.band_count} bands; the limit is {MAX_BANDS}.")
    if header.raw_bytes > MAX_RAW_BYTES:
        raise ConversionRefused(
            f"The input is {header.width} x {header.height} pixels with {header.band_count} band(s), about "
            f"{header.raw_bytes / 2**30:.0f} GiB uncompressed; the limit is {MAX_RAW_BYTES // 2**30} GiB.")


def required_free_bytes(input_bytes: int) -> int:
    return max(MIN_FREE_BYTES, int(input_bytes * DISK_HEADROOM_FACTOR))


def check_disk(work_dir: str, input_bytes: int) -> None:
    free = shutil.disk_usage(work_dir).free
    need = required_free_bytes(input_bytes)
    if free < need:
        raise ConversionRefused(
            f"Not enough ephemeral disk: the input is {input_bytes / 2**30:.1f} GiB and the conversion needs about "
            f"{need / 2**30:.1f} GiB beside it for the temporary and final COG, but only {free / 2**30:.1f} GiB is "
            f"free. Raise ephemeralStorageGiB in cogConvert-construct.ts.")


def check_download_disk(work_dir: str, object_bytes: int) -> None:
    """Before downloading: the object plus the headroom the conversion will need must fit."""
    free = shutil.disk_usage(work_dir).free
    need = int(object_bytes * (1 + DISK_HEADROOM_FACTOR))
    if free < max(need, MIN_FREE_BYTES):
        raise ConversionRefused(
            f"Not enough ephemeral disk: the input is {object_bytes / 2**30:.1f} GiB and download plus conversion "
            f"need about {need / 2**30:.1f} GiB, but only {free / 2**30:.1f} GiB is free.")


# --- the GDAL call --------------------------------------------------------------------------------------------------

def gdal_env(memory_bytes: int) -> Dict[str, str]:
    """The child's environment: a fixed allow-list, local files only. No remote filesystem is reachable (the
    arguments are two local paths), sidecar discovery beside the file is off, and the PROJ network is off."""
    env = {key: os.environ[key] for key in _GDAL_ENV_KEYS if key in os.environ}
    env.update({
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",  # no .ovr / .msk / .aux.xml sidecars, no directory listing
        "GDAL_PAM_ENABLED": "NO",
        "PROJ_NETWORK": "OFF",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".none",
        "GDAL_HTTP_TIMEOUT": "1",
        # Half the task memory for GDAL's block cache; the rest is the process, the warp buffers and the page cache.
        "GDAL_CACHEMAX": str(max(256, memory_bytes // 2 // 1024 ** 2)),
    })
    return env


def creation_options(params: Dict[str, str], cpus: int) -> List[str]:
    """The `-co` list of the COG driver for this profile."""
    options = [
        f"COMPRESS={params['compression']}",
        "PREDICTOR=YES",
        "OVERVIEWS=AUTO",
        "BIGTIFF=IF_SAFER",
        f"NUM_THREADS={max(1, cpus)}",
    ]
    if TILING_SCHEME:
        options.append(f"TILING_SCHEME={TILING_SCHEME}")
    else:
        options.append("BLOCKSIZE=512")
    return options


def build_command(input_path: str, output_path: str, params: Dict[str, str], cpus: int) -> List[str]:
    """The argument list for gdal_translate. Paths are absolute local files created by this process."""
    for path in (input_path, output_path):
        if not os.path.isabs(path) or "\x00" in path or path.startswith("/vsi"):
            raise ValueError("the GDAL arguments must be absolute local paths")
    command = ["gdal_translate", "-of", "COG", "-q"]
    for option in creation_options(params, cpus):
        command += ["-co", option]
    return command + [input_path, output_path]


def run_streaming(cmd, env=None, cwd=None, tail_lines: int = _TAIL_LINES) -> Tuple[int, str]:
    """Run `cmd`, forwarding its output to the log while keeping a bounded tail for the failure message.

    One pipe, drained as the child writes (reading before waiting is what avoids the deadlock), stderr merged
    into stdout so the unread pipe cannot fill."""
    tail = collections.deque(maxlen=tail_lines)
    proc = subprocess.Popen(  # nosec B603 nosemgrep: dangerous-subprocess-use-audit
        cmd, env=env, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for raw in iter(lambda: proc.stdout.readline(), b""):
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            print(line, flush=True)
            if line.strip():
                tail.append(line[:_TAIL_LINE_CHARS])
    finally:
        if proc.stdout:
            proc.stdout.close()
        proc.wait()
    return proc.returncode, "\n".join(tail)


def gdal_version() -> str:
    try:
        code, tail = run_streaming(["gdalinfo", "--version"], env=gdal_env(1 << 30), tail_lines=2)
    except OSError:
        return "unknown"
    match = re.search(r"GDAL\s+(\d+(?:\.\d+)+)", tail)
    return match.group(1) if (code == 0 and match) else "unknown"


def convert(input_path: str, output_path: str, params: Dict[str, str]) -> None:
    cpus = task_cpus()
    memory = task_memory_bytes()
    command = build_command(input_path, output_path, params, cpus)
    logger.info(f"Running GDAL with {cpus} thread(s), cache {gdal_env(memory)['GDAL_CACHEMAX']} MiB: {' '.join(command)}")
    code, tail = run_streaming(command, env=gdal_env(memory), cwd=os.path.dirname(output_path))
    if code != 0:
        raise RuntimeError(f"GDAL conversion failed with exit code {code}. Last output:\n{tail}")
    if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError("GDAL reported success but wrote no output file")


def validate_output(output_path: str) -> None:
    """Read the written file back: it must be a COG by the shared definition and in the output CRS."""
    from .tiff_header import HeaderRejected, read_header

    try:
        header = read_header(output_path)
    except HeaderRejected as error:
        raise RuntimeError(f"The converted file could not be read back: {error}") from error
    problems = []
    if not header.is_cog:
        problems.append("it is not laid out as a COG")
    if OUTPUT_CRS != "native" and f"EPSG:{header.epsg}" != OUTPUT_CRS and not (
            OUTPUT_CRS == "EPSG:3857" and header.is_web_mercator):
        problems.append(f"its CRS is {header.crs}, not {OUTPUT_CRS}")
    if not header.georeferenced:
        problems.append("it is not georeferenced")
    if problems:
        raise RuntimeError("The converted file failed validation: " + "; ".join(problems))


# --- attributes -----------------------------------------------------------------------------------------------------

def _row(key: str, value: str) -> dict:
    return {"metadataKey": f"{ATTRIBUTE_GROUP}_{key}", "metadataValue": value, "metadataValueType": "string"}


def attribute_document(rows: List[dict]) -> str:
    return json.dumps({"type": "attribute", "updateType": "update", "metadata": rows})


def cog_attributes(source_rel: str, source_version: str, compression: str, tool_version: str,
                   source_crs: str, output_crs: str) -> str:
    """The attributes on the COG: which file it was made from, at which version, and what it is."""
    rows = [_row("cogOf", source_rel)]
    if source_version:
        rows.append(_row("cogOfVersionId", source_version))
    rows += [_row("cogCompression", compression.lower()), _row("cogToolVersion", f"{TOOL_NAME} {tool_version}"),
             _row("sourceCrs", source_crs), _row("outputCrs", output_crs)]
    return attribute_document(rows)


def source_attributes(cog_rel: str) -> str:
    """The attribute on the source file that names its COG."""
    return attribute_document([_row("cogFile", cog_rel)])
