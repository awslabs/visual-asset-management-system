# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
3D Gaussian splat handler, CPU only.

* A 3DGS ``.ply`` (vertex element with ``f_dc_*``, ``opacity``, ``scale_*``, ``rot_*``) is read through
  a memory map, column by column.
* ``.splat`` (32-byte records: position, linear scale, RGBA, quantised rotation) is decoded in numpy.
* ``.spz``, ``.sog``, ``.ksplat``, ``.lcc`` and ``.compressed.ply`` are converted to a 3DGS PLY with
  the PlayCanvas ``splat-transform`` CLI (MIT), run on the CPU.

Each splat is drawn as a point Gaussian: colour ``0.5 + C0 * f_dc`` (the degree-0 spherical harmonic),
opacity ``sigmoid(opacity)``, size ``exp(max(scale))``. Splats below an opacity threshold are dropped
and the rest are sampled to the splat budget with probability proportional to opacity, so faint
"floaters" neither dominate the image nor the framing (the renderer frames point data on its 2nd-98th
percentile bounds, which over an opacity-weighted sample are opacity-weighted bounds).

Orientation: scenes trained with the original 3DGS code (COLMAP camera convention) are Y-down. The
handler turns every splat scene 180 degrees about Z, as SuperSplat does on load, so they render
upright.

``splat-transform`` is untrusted-input safe only within limits this module enforces (ARCC BSC1): it
executes ``.mjs`` generator scripts and downloads ``http(s)://`` inputs, so it is only ever given a
local regular file inside the job's work directory whose name ends in an allowlisted extension, is
started with an argument list (no shell) and a minimal environment, and is killed after a timeout.
"""

import os
import shutil
import subprocess  # nosec B404 - fixed executable, argument list, allowlisted local input
import tempfile
from typing import Optional

import numpy as np
import pyvista as pv

from ..utils.logging import get_logger
from . import ply_header

logger = get_logger()

SH_C0 = 0.28209479177387814
OPACITY_THRESHOLD = 0.2
DEFAULT_MAX_SPLATS = 4_000_000

# Native formats, and the formats converted by splat-transform. ``.compressed.ply`` is matched on the
# file name: its extension alone is ``.ply``.
NATIVE_EXTENSIONS = {".splat"}
CONVERTED_EXTENSIONS = {".spz", ".sog", ".ksplat", ".lcc"}
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
        directory = tempfile.mkdtemp(prefix="splat-", dir=root)
        try:
            source = file_path
            if compressed_ply and not needs_conversion(file_path):
                source = os.path.join(directory, "input.compressed.ply")
                try:
                    os.link(file_path, source)
                except OSError:
                    shutil.copyfile(file_path, source)
            converted = convert_to_ply(source, directory, root=root)
            return load_ply(converted, max_splats)
        finally:
            shutil.rmtree(directory, ignore_errors=True)
    if name.endswith(".splat"):
        return load_splat(file_path, max_splats)
    return load_ply(file_path, max_splats)


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
                   timeout: int = CONVERSION_TIMEOUT_SECONDS) -> str:
    """Convert a splat file to a 3DGS PLY in ``out_dir`` with splat-transform on the CPU."""
    source = validate_conversion_input(path, root)
    output = os.path.join(os.path.realpath(out_dir), "converted.ply")
    command = [NODE_BINARY, SPLAT_TRANSFORM_CLI, "--quiet", "--overwrite", "--gpu", "cpu", source, output]
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


def _to_polydata(xyz, rgb01, alpha, radius) -> pv.PolyData:
    xyz = np.asarray(xyz, dtype=np.float64)
    # 180 degrees about Z: the 3DGS (COLMAP) Y-down convention to Y-up.
    xyz[:, 0] *= -1.0
    xyz[:, 1] *= -1.0
    cloud = pv.PolyData(np.ascontiguousarray(xyz))
    cloud.point_data["RGB"] = np.ascontiguousarray(np.clip(rgb01 * 255.0 + 0.5, 0, 255).astype(np.uint8))
    cloud.point_data["splat_radius"] = np.ascontiguousarray(radius, dtype=np.float32)
    cloud.point_data["splat_opacity"] = np.ascontiguousarray(alpha, dtype=np.float32)
    return cloud


def _finish(xyz, rgb01, alpha, radius, total: int) -> pv.PolyData:
    if not len(xyz):
        raise ValueError("No splats above the opacity threshold")
    logger.info(f"Loaded {len(xyz):,} of {total:,} splats (opacity >= {OPACITY_THRESHOLD})")
    return _to_polydata(xyz, rgb01, alpha, radius)


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
    radius = np.exp(np.clip(log_scale.max(axis=1), -30, 10))
    finite = np.isfinite(xyz).all(axis=1) & np.isfinite(rgb).all(axis=1) & np.isfinite(radius)
    return _finish(xyz[finite], np.clip(rgb[finite], 0, 1), alpha[keep][finite], radius[finite], total)


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
    radius = rows["scale"].astype(np.float64).max(axis=1)
    rgb = rows["rgba"][:, :3].astype(np.float64) / 255.0
    finite = np.isfinite(xyz).all(axis=1) & np.isfinite(radius)
    return _finish(xyz[finite], rgb[finite], alpha[keep][finite], radius[finite], len(records))


def is_splat(pv_data) -> bool:
    return "splat_radius" in getattr(pv_data, "point_data", {})
