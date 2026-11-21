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
# A WGS84 interval at least this wide is a whole-world raster, not an antimeridian crossing.
NEAR_GLOBAL_DEGREES = 350.0

# --- limits ---------------------------------------------------------------------------------------------------------
# Refused before any GDAL work. The raw figure is width x height x bands x bytes per sample, uncompressed.
MAX_RAW_BYTES = 256 * 1024 ** 3
MAX_INPUT_BYTES = 100 * 1024 ** 3
MAX_BANDS = 16
# What the volume must hold beyond the downloaded input: the COG driver writes a temporary file and then the final
# one, each at most the UNCOMPRESSED size of the output (a compressed JPEG input measured 9.5x its own size once it
# became an RGBA DEFLATE COG, so the input's size says nothing about the output's). The output grid is estimated
# from a warped VRT of the input (`estimate_output_pixels`) and rounded up for the zoom level GDAL picks.
SCRATCH_FACTOR = 2
ZOOM_ROUNDING_FACTOR = 2   # GDAL picks the closest zoom level: at most about 1.41x per axis, 2x in pixels
TILE_PADDING_PIXELS = 512  # a tile of padding on each side of each axis
MIN_FREE_BYTES = 1 * 1024 ** 3
# The Fargate ephemeral volume the job definition declares (cogConvert-construct.ts), named in refusals.
VOLUME_GIB = 200

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

def check_input_size(input_bytes: int) -> None:
    """Refuse an object over the size limit before it is downloaded."""
    if input_bytes > MAX_INPUT_BYTES:
        raise ConversionRefused(
            f"The input is {input_bytes / 2**30:.1f} GiB; the limit is {MAX_INPUT_BYTES // 2**30} GiB.")


def check_limits(header, input_bytes: int) -> None:
    """Refuse a file over the pixel, size or band limits, naming the limit."""
    check_input_size(input_bytes)
    if header.band_count > MAX_BANDS:
        raise ConversionRefused(f"The input has {header.band_count} bands; the limit is {MAX_BANDS}.")
    if header.raw_bytes > MAX_RAW_BYTES:
        raise ConversionRefused(
            f"The input is {header.width} x {header.height} pixels with {header.band_count} band(s), about "
            f"{header.raw_bytes / 2**30:.0f} GiB uncompressed; the limit is {MAX_RAW_BYTES // 2**30} GiB.")


def estimate_output_pixels(width: int, height: int, warped_size) -> int:
    """Upper bound of the output grid in pixels: the larger of the warped VRT's grid and the source grid (a world
    or polar extent warps to a degenerate VRT), rounded up for GDAL's zoom choice, plus tile padding."""
    warped = (int(warped_size[0]) * int(warped_size[1])) if warped_size else 0
    base = max(warped, int(width) * int(height))
    side = int(base ** 0.5) + TILE_PADDING_PIXELS
    return max(base * ZOOM_ROUNDING_FACTOR, 0) + 2 * side * TILE_PADDING_PIXELS


def estimate_output_bytes(header, pixels: int) -> int:
    """Uncompressed size of the output: its pixels x (source bands + 1 alpha band) x the sample size."""
    return pixels * (header.band_count + 1) * max(1, header.bits_per_sample // 8)


def required_scratch_bytes(output_bytes: int) -> int:
    return max(MIN_FREE_BYTES, SCRATCH_FACTOR * output_bytes)


def check_download_disk(work_dir: str, object_bytes: int) -> None:
    """Before downloading: the object itself (and a gibibyte of working room) must fit; the output is checked once
    the header tells the size."""
    free = shutil.disk_usage(work_dir).free
    if free < object_bytes + MIN_FREE_BYTES:
        raise ConversionRefused(
            f"The input is {object_bytes / 2**30:.1f} GiB and does not fit with room to work on the task's "
            f"{VOLUME_GIB} GiB volume ({free / 2**30:.1f} GiB free).")


def check_disk(work_dir: str, output_bytes: int, input_bytes: int) -> None:
    """After the download and the size estimate: the temporary and final COG must fit beside the input."""
    free = shutil.disk_usage(work_dir).free
    need = required_scratch_bytes(output_bytes)
    if free < need:
        raise ConversionRefused(
            f"Not enough disk for this file: the input is {input_bytes / 2**30:.1f} GiB and the Cloud Optimized "
            f"GeoTIFF is estimated at up to {output_bytes / 2**30:.1f} GiB uncompressed, so about "
            f"{need / 2**30:.1f} GiB is needed for its temporary and final copies, but only {free / 2**30:.1f} GiB "
            f"is free on the task's {VOLUME_GIB} GiB volume (the Fargate maximum). Split or downsample the file.")


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
    _children.add(proc)
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
        _children.discard(proc)
    return proc.returncode, "\n".join(tail)


# The GDAL processes this container has running, so a SIGTERM can stop them (see __main__).
_children = set()


def kill_children() -> None:
    """Kill every GDAL child still running. Safe to call from a signal handler."""
    for proc in list(_children):
        try:
            proc.kill()
        except OSError:
            pass


def run_capture(cmd, env=None, timeout: int = 300, limit: int = 4 * 1024 * 1024) -> str:
    """Run a short GDAL utility and return its standard output (at most `limit` bytes). An argument list, no shell;
    a non-zero exit raises RuntimeError with the tail of the error output."""
    proc = subprocess.run(  # nosec B603 nosemgrep: dangerous-subprocess-use-audit
        cmd, env=env, capture_output=True, timeout=timeout, check=False)
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-5:]
        raise RuntimeError(f"{cmd[0]} failed with exit code {proc.returncode}: " + " | ".join(tail))
    return proc.stdout[:limit].decode("utf-8", "replace")


def gdal_version() -> str:
    try:
        code, tail = run_streaming(["gdalinfo", "--version"], env=gdal_env(1 << 30), tail_lines=2)
    except OSError:
        return "unknown"
    match = re.search(r"GDAL\s+(\d+(?:\.\d+)+)", tail)
    return match.group(1) if (code == 0 and match) else "unknown"


def inspect(path: str, memory_bytes: int = 1 << 30) -> dict:
    """`gdalinfo -json` of a local file: the WGS84 extent GDAL derives from the geotransform and the CRS, and the
    per-band nodata and colour interpretation. {} when GDAL cannot describe the file."""
    try:
        return json.loads(run_capture(["gdalinfo", "-json", "-nomd", path], env=gdal_env(memory_bytes)))
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        logger.warning(f"gdalinfo could not describe the input: {error}")
        return {}


def crosses_antimeridian(info: dict) -> bool:
    """Whether the data's WGS84 extent straddles longitude 180 without being a whole-world raster.

    GDAL reports the extent ring in the source's own longitude convention, so a crossing shows as a jump (178.7 to
    -178.9) and a 0-360 raster as values above 180. The ring is unwrapped into one continuous interval; an interval
    that contains 180 and is narrower than a near-global width crosses. Such a file warps to a world-wide output that
    holds a few valid pixels, so it is refused rather than converted."""
    ring = ((info.get("wgs84Extent") or {}).get("coordinates") or [[]])[0]
    lons = [float(point[0]) for point in ring if len(point) >= 2]
    if not lons:
        return False
    if max(lons) - min(lons) > 180 and any(lon < 0 for lon in lons):
        lons = [lon + 360 if lon < 0 else lon for lon in lons]
    low, high = min(lons), max(lons)
    return low < 180 < high and (high - low) < NEAR_GLOBAL_DEGREES


ANTIMERIDIAN_MESSAGE = (
    "This raster crosses the antimeridian (longitude 180), which COG conversion does not support: in Web Mercator "
    "it would become a world-wide grid of almost empty pixels. Split the file at longitude 180 into a western and "
    "an eastern part (for example with gdal_translate -projwin on each side), upload the parts, and convert each one."
)


def check_antimeridian(info: dict) -> None:
    if crosses_antimeridian(info):
        raise ConversionRefused(ANTIMERIDIAN_MESSAGE)


def needs_mask_band(info: dict) -> bool:
    """A source with a nodata value and no alpha band is converted through a mask band, not a destination nodata:
    GDAL's warper rewrites every valid destination pixel equal to the nodata value to nodata + 1."""
    bands = info.get("bands") or []
    if not bands or any(b.get("colorInterpretation") in ("Alpha", "Palette") for b in bands):
        return False
    return any("noDataValue" in b for b in bands)


def mask_vrt_command(input_path: str, vrt_path: str, band_count: int) -> List[str]:
    """`gdal_translate` arguments for a VRT of the data bands plus the nodata mask as an alpha band, with no nodata
    value on the data bands."""
    command = ["gdal_translate", "-q", "-of", "VRT"]
    for band in range(1, band_count + 1):
        command += ["-b", str(band)]
    command += ["-b", "mask", "-a_nodata", "none", f"-colorinterp_{band_count + 1}", "alpha", input_path, vrt_path]
    return command


def prepare_source(input_path: str, work_dir: str, info: dict, memory_bytes: int) -> str:
    """The file GDAL converts: the input itself, or a VRT over it that carries the nodata mask as alpha."""
    if not needs_mask_band(info):
        return input_path
    vrt_path = os.path.join(work_dir, "masked.vrt")
    run_capture(mask_vrt_command(input_path, vrt_path, len(info["bands"])), env=gdal_env(memory_bytes))
    logger.info("Source has a nodata value: converting through a mask band so valid pixels equal to it are kept")
    return vrt_path


def warped_size(source_path: str, work_dir: str, memory_bytes: int):
    """(width, height) of the input warped to the output CRS at GDAL's own resolution, from a VRT (no pixels are
    read); None when GDAL cannot warp it, in which case the estimate falls back to the source grid."""
    vrt_path = os.path.join(work_dir, "warped.vrt")
    try:
        run_capture(["gdalwarp", "-q", "-of", "VRT", "-t_srs", OUTPUT_CRS, source_path, vrt_path],
                    env=gdal_env(memory_bytes))
        size = json.loads(run_capture(["gdalinfo", "-json", "-nomd", vrt_path], env=gdal_env(memory_bytes)))["size"]
        return int(size[0]), int(size[1])
    except (OSError, RuntimeError, ValueError, KeyError, IndexError, subprocess.TimeoutExpired) as error:
        logger.warning(f"The warped size could not be estimated: {error}")
        return None


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
