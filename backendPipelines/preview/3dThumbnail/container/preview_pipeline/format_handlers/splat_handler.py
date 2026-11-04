# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
3D Gaussian splat handler, CPU only.

* A 3DGS ``.ply`` (vertex element with ``f_dc_*``, ``opacity``, ``scale_*``, ``rot_*``) is read through
  a memory map, column by column.
* ``.splat`` (32-byte records: position, linear scale, RGBA, quantised rotation) is decoded in numpy.
* ``.spz``, ``.sog``, ``.ksplat``, ``.lcc``, ``.lcc2`` and ``.compressed.ply`` are converted to a 3DGS
  PLY with the PlayCanvas ``splat-transform`` CLI (MIT), run on the CPU. An XGRIDS ``.lcc``/``.lcc2``
  scene is read at one level of detail (``lcc_meta.select_level``).

Each splat is drawn as a flat, unlit disc in its degree-0 spherical-harmonic colour
(``0.5 + C0 * f_dc``). Its radius gives the disc the coverage of the splat's Gaussian:
``sigma * sqrt(2 * alpha)``, where ``sigma = sqrt(s_max * s_mid)`` (the two larger scales) and
``alpha = sigmoid(opacity)``. Splats below an opacity threshold are dropped and the rest are sampled to
the splat budget with probability proportional to opacity.

Real captures surround their subject with background (room walls, sky, distant trees) made of large
splats, and percentile framing of the whole scene puts the camera among them. The handler therefore
keeps the scene's core: the splats within ``CORE_KEEP_FACTOR`` times the ``CORE_QUANTILE`` distance from
the detail-weighted median (detail weight ``alpha / sigma**2``: small opaque splats sit where the
capture looked closely). A compact object (a ball, a rod, a slab) loses nothing, since all of it lies
within 1.75 times its own 60th-percentile distance. In the core, splats much larger than the scene
(``> LARGE_SPLAT_FRACTION`` of the core radius and ``> LARGE_SPLAT_MEDIAN_FACTOR`` times the median
footprint) are dropped, and a flat core (a ground plane) tilted by less than ``LEVEL_MAX_TILT_DEG`` is
levelled so the orbit circles its up axis.

Orientation: scenes trained with the original 3DGS code (COLMAP camera convention) are Y-down. The
handler turns every splat scene 180 degrees about Z, as SuperSplat does on load, so they render
upright.

``splat-transform`` is untrusted-input safe only within limits this module enforces (ARCC BSC1): it
executes ``.mjs`` generator scripts and downloads ``http(s)://`` inputs, so it is only ever given a
local regular file inside the job's work directory whose name ends in an allowlisted extension, is
started with an argument list (no shell) and a minimal environment, and is killed after a timeout. An
``.lcc2`` description names its chunk files; every name must be a relative path that stays inside the
work directory, or the conversion is refused before the CLI starts.
"""

import os
import re
import shutil
import subprocess  # nosec B404 - fixed executable, argument list, allowlisted local input
import tempfile
from typing import Optional, Tuple

import numpy as np
import pyvista as pv

from ..utils.logging import get_logger
from . import lcc_meta, ply_header

logger = get_logger()

_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")

SH_C0 = 0.28209479177387814
OPACITY_THRESHOLD = 0.2
DEFAULT_MAX_SPLATS = lcc_meta.DEFAULT_MAX_SPLATS

# Scene core: splats within CORE_KEEP_FACTOR times the CORE_QUANTILE (detail-weighted) distance from
# the detail-weighted median are kept. The 1.75 factor keeps all of a uniform rod, the most elongated
# compact shape (its farthest point is 1/0.6 = 1.67 times its 60th-percentile distance).
CORE_QUANTILE = 0.6
CORE_KEEP_FACTOR = 1.75
# A splat whose disc radius exceeds both of these (relative to the core radius and to the median disc
# radius) is background fog, not detail.
LARGE_SPLAT_FRACTION = 0.03
LARGE_SPLAT_MEDIAN_FACTOR = 10.0
# A core whose smallest spread is under this share of the middle one is a plane (ground, floor) ...
LEVEL_MAX_FLATNESS = 0.35
# ... and is levelled when its normal is within this angle of the up axis.
LEVEL_MAX_TILT_DEG = 60.0
# Below this many splats a scene is drawn whole.
MIN_SPLATS_FOR_FOCUS = 1000

# Native formats, and the formats converted by splat-transform. ``.compressed.ply`` is matched on the
# file name: its extension alone is ``.ply``.
NATIVE_EXTENSIONS = {".splat"}
CONVERTED_EXTENSIONS = {".spz", ".sog", ".ksplat", ".lcc", ".lcc2"}
CONVERTED_SUFFIXES = (".compressed.ply",) + tuple(sorted(CONVERTED_EXTENSIONS))
SUPPORTED_EXTENSIONS = NATIVE_EXTENSIONS | CONVERTED_EXTENSIONS

SPLAT_TRANSFORM_CLI = os.environ.get(
    "SPLAT_TRANSFORM_CLI", "/opt/splat-transform/node_modules/@playcanvas/splat-transform/bin/cli.mjs")
NODE_BINARY = os.environ.get("SPLAT_TRANSFORM_NODE", "/opt/node/bin/node")
CONVERSION_TIMEOUT_SECONDS = 1800


class SplatConversionError(Exception):
    pass


def can_handle(extension: str) -> bool:
    return extension.lower() in SUPPORTED_EXTENSIONS


def is_compressed_ply(path: str) -> bool:
    return os.path.basename(path).lower().endswith(".compressed.ply")


def needs_conversion(path: str) -> bool:
    return os.path.basename(path).lower().endswith(CONVERTED_SUFFIXES)


def load(file_path: str, max_splats: int = DEFAULT_MAX_SPLATS, work_dir: Optional[str] = None,
         compressed_ply: bool = False) -> pv.PolyData:
    """Load a splat file of any supported format as point Gaussians within ``max_splats``.

    ``compressed_ply`` marks a ``.ply`` whose header shows the compressed-PLY layout although its name
    lacks the ``.compressed.ply`` suffix; it is linked under that suffix for the conversion.
    """
    name = os.path.basename(file_path).lower()
    logger.info(f"Loading Gaussian splats: {file_path} (budget {max_splats:,} splats)")
    if needs_conversion(file_path) or compressed_ply:
        root = work_dir or os.path.dirname(os.path.abspath(file_path))
        if name.endswith(".sog"):
            from ..utils.archive import check_archive

            # A .sog bundle is a zip: its uncompressed size must fit the disk before it is unpacked.
            check_archive(file_path, root)
        lod = None
        if name.endswith(".lcc2"):
            lod = prepare_lcc2(file_path, root, max_splats)
        elif name.endswith(".lcc"):
            lod = select_lcc_level(file_path, max_splats)
        directory = tempfile.mkdtemp(prefix="splat-", dir=root)
        try:
            source = file_path
            if compressed_ply and not needs_conversion(file_path):
                source = os.path.join(directory, "input.compressed.ply")
                try:
                    os.link(file_path, source)
                except OSError:
                    shutil.copyfile(file_path, source)
            converted = convert_to_ply(source, directory, root=root, lod=lod)
            return load_ply(converted, max_splats)
        finally:
            shutil.rmtree(directory, ignore_errors=True)
    if name.endswith(".splat"):
        return load_splat(file_path, max_splats)
    return load_ply(file_path, max_splats)


# --------------------------------------------------------------------------------------------------
# XGRIDS LCC levels of detail
# --------------------------------------------------------------------------------------------------

def select_lcc_level(path: str, max_splats: int) -> Optional[int]:
    """The level an ``.lcc`` scene is read at, or None (the converter's default, LOD 0) when its
    description gives no per-level counts."""
    try:
        counts = lcc_meta.lcc_level_counts(lcc_meta.read_meta(path))
    except (OSError, lcc_meta.LccMetaError) as e:
        logger.warning(f"Could not read the LCC levels of detail ({e}); reading LOD 0")
        return None
    if not counts:
        return None
    level = lcc_meta.select_level(counts, max_splats)
    logger.info(f"LCC levels of detail (splats): {counts}; reading LOD {level}")
    return level


def _chunk_path(base: str, name: str) -> Optional[str]:
    """The path splat-transform opens for an ``.lcc2`` chunk name (``path.join(dir, name)``), or None
    when the name is not a plain relative path: absolute, a URL or scheme, a ``..`` escape, a
    backslash, a control character or over-long."""
    if (not isinstance(name, str) or not name or len(name) > 1024 or "\\" in name
            or name.startswith("/") or "://" in name or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in name)
            or _SCHEME.match(name)):
        return None
    joined = os.path.normpath(os.path.join(base, name))
    if not joined.startswith(base + os.sep):
        return None
    return joined


def prepare_lcc2(path: str, root: str, max_splats: int) -> int:
    """Check an ``.lcc2`` description before splat-transform reads it and return the level to read.

    Every chunk file the description lists must resolve inside the job's work directory ``root``
    (the converter opens them by path), and every chunk of the chosen level must be present; otherwise
    SplatConversionError, before the converter starts.
    """
    try:
        meta = lcc_meta.read_meta(path)
        names = lcc_meta.all_lcc2_files(meta)
        level, level_names = lcc_meta.lcc2_level_files(meta, max_splats)
        counts = lcc_meta.lcc2_level_counts(meta)
    except (OSError, lcc_meta.LccMetaError) as e:
        raise SplatConversionError(f"Unreadable LCC2 scene description: {e}")
    base = os.path.realpath(os.path.dirname(os.path.abspath(path)))
    real_root = os.path.realpath(root)
    for name in names:
        joined = _chunk_path(base, name)
        if joined is None or not os.path.realpath(joined).startswith(real_root + os.sep):
            raise SplatConversionError(f"LCC2 chunk file name {name[:200]!r} is not a path inside the asset")
    missing = [name for name in level_names if not os.path.isfile(_chunk_path(base, name))]
    if missing:
        raise SplatConversionError(
            f"{len(missing)} of {len(level_names)} LCC2 chunk files for LOD {level} are not in the asset "
            f"beside the scene description, for example {missing[0][:200]!r}")
    logger.info(f"LCC2 levels of detail (splats): {counts}; reading LOD {level} "
                f"({len(level_names)} chunk files)")
    return level


# --------------------------------------------------------------------------------------------------
# Conversion
# --------------------------------------------------------------------------------------------------

def validate_conversion_input(path: str, root: Optional[str] = None) -> str:
    """The real path of a file splat-transform may read, or SplatConversionError.

    The input must be a local regular file, inside ``root`` when given, whose name ends in an
    allowlisted splat extension: never a URL (fetched by splat-transform) and never ``.mjs``
    (executed by it).
    """
    if not isinstance(path, str) or not path or "\x00" in path or "://" in path:
        raise SplatConversionError("splat-transform input must be a local file path")
    real = os.path.realpath(path)
    if not real.lower().endswith(CONVERTED_SUFFIXES) or real.lower().endswith(".mjs"):
        raise SplatConversionError(f"splat-transform input {os.path.basename(path)!r} has no allowlisted extension")
    if root is not None:
        base = os.path.realpath(root)
        if not real.startswith(base + os.sep):
            raise SplatConversionError("splat-transform input is outside the job's work directory")
    if not os.path.isfile(real):
        raise SplatConversionError("splat-transform input is not a regular file")
    return real


def convert_to_ply(path: str, out_dir: str, root: Optional[str] = None,
                   timeout: int = CONVERSION_TIMEOUT_SECONDS, lod: Optional[int] = None) -> str:
    """Convert a splat file to a 3DGS PLY in ``out_dir`` with splat-transform on the CPU; ``lod``
    selects one level of an LCC/LCC2 scene."""
    source = validate_conversion_input(path, root)
    output = os.path.join(os.path.realpath(out_dir), "converted.ply")
    command = [NODE_BINARY, SPLAT_TRANSFORM_CLI, "--quiet", "--overwrite", "--gpu", "cpu"]
    if lod is not None:
        command += ["--select-lod", str(int(lod))]
    command += [source, output]
    environment = {"PATH": "/usr/bin:/bin", "HOME": out_dir, "NODE_OPTIONS": ""}
    logger.info(f"Converting {os.path.basename(source)} to PLY with splat-transform")
    try:
        completed = subprocess.run(  # nosec B603 - argument list, no shell, validated input
            command, cwd=out_dir, env=environment, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise SplatConversionError(f"splat-transform did not finish within {timeout} s")
    except OSError as e:
        raise SplatConversionError(f"splat-transform could not start: {e}")
    if completed.returncode != 0 or not os.path.isfile(output):
        detail = (completed.stderr or completed.stdout or b"").decode("utf-8", errors="replace")[-2000:]
        raise SplatConversionError(f"splat-transform failed (exit {completed.returncode}): {detail}")
    return output


# --------------------------------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------------------------------

def sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(values, -60, 60)))


def opacity_weighted_sample(alpha: np.ndarray, budget: int, seed: int = 42) -> np.ndarray:
    """Sorted indices of at most ``budget`` entries with ``alpha >= OPACITY_THRESHOLD``, drawn without
    replacement with probability proportional to ``alpha`` (Efraimidis-Spirakis keys)."""
    candidates = np.flatnonzero(alpha >= OPACITY_THRESHOLD)
    if len(candidates) <= budget:
        return candidates
    rng = np.random.default_rng(seed)
    keys = np.log(rng.random(len(candidates))) / alpha[candidates]
    chosen = np.argpartition(keys, len(candidates) - budget)[len(candidates) - budget:]
    return np.sort(candidates[chosen])


def _weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    order = np.argsort(values)
    cumulative = np.cumsum(weights[order])
    index = min(int(np.searchsorted(cumulative, q * cumulative[-1])), len(values) - 1)
    return float(values[order][index])


def _rotation_onto(normal: np.ndarray, up: np.ndarray) -> np.ndarray:
    """The rotation matrix turning unit vector ``normal`` onto unit vector ``up`` (Rodrigues)."""
    axis = np.cross(normal, up)
    sine = float(np.linalg.norm(axis))
    cosine = float(normal @ up)
    if sine < 1e-9:
        return np.eye(3)
    k = np.array([[0.0, -axis[2], axis[1]], [axis[2], 0.0, -axis[0]], [-axis[1], axis[0], 0.0]])
    return np.eye(3) + k + k @ k * ((1.0 - cosine) / sine ** 2)


def focus_scene(xyz: np.ndarray, sigma: np.ndarray, alpha: np.ndarray, footprint: np.ndarray
                ) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]:
    """(keep mask, centre, rotation or None) for the scene's core, in the file's frame (Y down).

    Keeps the splats within CORE_KEEP_FACTOR times the detail-weighted CORE_QUANTILE distance from the
    detail-weighted median, drops core splats far larger than the scene, and returns the rotation
    that levels a flat, slightly tilted core.
    """
    keep = np.ones(len(xyz), dtype=bool)
    if len(xyz) < MIN_SPLATS_FOR_FOCUS:
        return keep, np.zeros(3), None
    weight = alpha / np.maximum(sigma, np.percentile(sigma, 5)) ** 2
    centre = np.array([_weighted_quantile(xyz[:, axis], weight, 0.5) for axis in range(3)])
    distance = np.linalg.norm(xyz - centre, axis=1)
    core_radius = _weighted_quantile(distance, weight, CORE_QUANTILE)
    if core_radius <= 0:
        return keep, centre, None
    keep = distance <= CORE_KEEP_FACTOR * core_radius
    large = footprint > max(LARGE_SPLAT_FRACTION * core_radius,
                            LARGE_SPLAT_MEDIAN_FACTOR * float(np.median(footprint[keep])))
    keep &= ~large
    logger.info(f"Splat scene core: radius {core_radius:.3g} around {np.round(centre, 3).tolist()}; "
                f"kept {int(keep.sum()):,} of {len(xyz):,} splats "
                f"({int(np.sum(distance > CORE_KEEP_FACTOR * core_radius)):,} outside the core, "
                f"{int(np.sum(large & (distance <= CORE_KEEP_FACTOR * core_radius))):,} oversized)")
    rotation = None
    if keep.sum() >= MIN_SPLATS_FOR_FOCUS:
        offsets = xyz[keep] - centre
        values, vectors = np.linalg.eigh(np.cov(offsets.T, aweights=weight[keep]))
        up = np.array([0.0, -1.0, 0.0])  # file -Y is up once the scene is turned about Z
        normal = vectors[:, 0] if vectors[:, 0] @ up >= 0 else -vectors[:, 0]
        flatness = float(values[0] / values[1]) if values[1] > 0 else 1.0
        tilt = float(np.degrees(np.arccos(np.clip(normal @ up, -1.0, 1.0))))
        if flatness < LEVEL_MAX_FLATNESS and 0.5 < tilt < LEVEL_MAX_TILT_DEG:
            rotation = _rotation_onto(normal, up)
            logger.info(f"Levelling the splat scene's ground plane: tilted {tilt:.1f} degrees "
                        f"(flatness {flatness:.3f})")
    return keep, centre, rotation


def _to_polydata(xyz, rgb01, alpha, footprint) -> pv.PolyData:
    xyz = np.asarray(xyz, dtype=np.float64)
    # 180 degrees about Z: the 3DGS (COLMAP) Y-down convention to Y-up.
    xyz[:, 0] *= -1.0
    xyz[:, 1] *= -1.0
    cloud = pv.PolyData(np.ascontiguousarray(xyz))
    cloud.point_data["RGB"] = np.ascontiguousarray(np.clip(rgb01 * 255.0 + 0.5, 0, 255).astype(np.uint8))
    cloud.point_data["splat_radius"] = np.ascontiguousarray(footprint, dtype=np.float32)
    cloud.point_data["splat_opacity"] = np.ascontiguousarray(alpha, dtype=np.float32)
    return cloud


def _finish(xyz, rgb01, alpha, scales, total: int) -> pv.PolyData:
    """Point Gaussians from per-splat positions, colours, opacities and linear scales (n x 3)."""
    if not len(xyz):
        raise ValueError("No splats above the opacity threshold")
    logger.info(f"Loaded {len(xyz):,} of {total:,} splats (opacity >= {OPACITY_THRESHOLD})")
    ordered = np.sort(scales, axis=1)
    sigma = np.sqrt(ordered[:, 2] * ordered[:, 1])
    footprint = sigma * np.sqrt(2.0 * alpha)
    keep, centre, rotation = focus_scene(xyz, sigma, alpha, footprint)
    xyz, rgb01, alpha, footprint = xyz[keep], rgb01[keep], alpha[keep], footprint[keep]
    if rotation is not None:
        xyz = (xyz - centre) @ rotation.T + centre
    return _to_polydata(xyz, rgb01, alpha, footprint)


def load_ply(path: str, max_splats: int = DEFAULT_MAX_SPLATS) -> pv.PolyData:
    header = ply_header.read_header(path)
    if header.kind != ply_header.KIND_SPLAT:
        raise ValueError("PLY file holds no Gaussian splat properties")
    vertices = ply_header.vertex_memmap(path, header)
    total = len(vertices)
    alpha = sigmoid(np.asarray(vertices["opacity"], dtype=np.float64))
    keep = opacity_weighted_sample(alpha, max_splats)
    rows = vertices[keep]
    xyz = np.column_stack([rows["x"], rows["y"], rows["z"]]).astype(np.float64)
    rgb = 0.5 + SH_C0 * np.column_stack([rows["f_dc_0"], rows["f_dc_1"], rows["f_dc_2"]]).astype(np.float64)
    log_scale = np.column_stack([rows["scale_0"], rows["scale_1"], rows["scale_2"]]).astype(np.float64)
    scales = np.exp(np.clip(log_scale, -30, 10))
    finite = np.isfinite(xyz).all(axis=1) & np.isfinite(rgb).all(axis=1) & np.isfinite(scales).all(axis=1)
    return _finish(xyz[finite], np.clip(rgb[finite], 0, 1), alpha[keep][finite], scales[finite], total)


SPLAT_RECORD = np.dtype([("position", "<f4", 3), ("scale", "<f4", 3), ("rgba", "u1", 4), ("rotation", "u1", 4)])


def load_splat(path: str, max_splats: int = DEFAULT_MAX_SPLATS) -> pv.PolyData:
    """The antimatter15 ``.splat`` layout: 32-byte records, linear scales, 8-bit colour and opacity."""
    size = os.path.getsize(path)
    if size == 0 or size % SPLAT_RECORD.itemsize:
        raise ValueError(f".splat size {size} is not a multiple of {SPLAT_RECORD.itemsize} bytes")
    records = np.memmap(path, dtype=SPLAT_RECORD, mode="r")
    alpha = records["rgba"][:, 3].astype(np.float64) / 255.0
    keep = opacity_weighted_sample(alpha, max_splats)
    rows = records[keep]
    xyz = rows["position"].astype(np.float64)
    scales = np.abs(rows["scale"].astype(np.float64))
    rgb = rows["rgba"][:, :3].astype(np.float64) / 255.0
    finite = np.isfinite(xyz).all(axis=1) & np.isfinite(scales).all(axis=1)
    return _finish(xyz[finite], rgb[finite], alpha[keep][finite], scales[finite], len(records))


def is_splat(pv_data) -> bool:
    return "splat_radius" in getattr(pv_data, "point_data", {})
