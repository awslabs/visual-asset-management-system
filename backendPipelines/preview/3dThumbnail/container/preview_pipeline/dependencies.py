# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Referenced files: fetch the files an input names (glTF buffers and images, USD sublayers, references,
payloads and asset-valued attributes, OBJ material libraries and their texture maps, FBX texture
files) from the input's own asset, so the loader opens the input against them.

Every reference is untrusted input. The container's job role can read every asset bucket, so a
reference must not pull another asset's content into this thumbnail (ARCC BSC1, input validation by
allowlist). A reference is accepted only when it is a relative path that, normalised against the
referring file's directory, stays inside the input's asset prefix; absolute paths, ``..`` escapes,
URI schemes (``s3:``, ``http(s):``, ``file:``, ``anon:``, a Windows drive), control characters and
over-long values are rejected. The fetch is bounded by a file count, a byte total (each object's size
is read with HeadObject before it is downloaded and checked against free disk) and a recursion depth
for formats whose fetched files reference further files. A rejected, missing or over-budget
reference is logged and skipped; it never fails the run.
"""

import json
import os
import posixpath
import re
import shutil
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional
from urllib.parse import unquote

from .utils import s3_utils as s3
from .utils.logging import get_logger

logger = get_logger()

DEFAULT_MAX_FILES = 2000
DEFAULT_MAX_TOTAL_BYTES = 50 * 1024 ** 3
DEFAULT_MAX_DEPTH = 32
# Free disk kept beyond a download, for the loader, the frames and the encoded output.
DEFAULT_DISK_HEADROOM_BYTES = 1024 ** 3
# S3 keys are at most 1,024 bytes; a longer reference cannot name an object.
MAX_REFERENCE_LENGTH = 1024

_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
_USD_LAYER_EXTENSIONS = {".usd", ".usda", ".usdc"}


@dataclass
class FetchLimits:
    max_files: int = DEFAULT_MAX_FILES
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES
    max_depth: int = DEFAULT_MAX_DEPTH
    disk_headroom_bytes: int = DEFAULT_DISK_HEADROOM_BYTES


@dataclass
class FetchReport:
    fetched: List[str] = field(default_factory=list)
    rejected: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    skipped_for_budget: List[str] = field(default_factory=list)
    total_bytes: int = 0


# --------------------------------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------------------------------

def asset_root_key(input_key: str, asset_id: str = "", output_dir: str = "",
                   same_bucket_output: bool = False) -> str:
    """The S3 prefix (ending in ``/``, or empty) references are confined to.

    An original asset file sits under ``.../<assetId>/...``: the root is the key up to and including
    the asset id segment. A file an earlier workflow step wrote sits in this run's output files
    folder, keyed by its asset-relative path: the root is that folder. Otherwise the root is the
    input file's own directory, the narrowest confinement.
    """
    input_key = input_key or ""
    if same_bucket_output and output_dir and input_key.startswith(output_dir):
        return output_dir if output_dir.endswith("/") else output_dir + "/"
    parts = input_key.split("/")
    if asset_id and asset_id in parts[:-1]:
        index = parts.index(asset_id)
        return "/".join(parts[:index + 1]) + "/"
    return input_key.rsplit("/", 1)[0] + "/" if "/" in input_key else ""


def relative_to_root(key: str, root: str) -> str:
    """``key`` relative to the confinement root (the input file's asset-relative path)."""
    if root and not key.startswith(root):
        raise ValueError(f"key {key!r} is not under {root!r}")
    return key[len(root):]


def local_path_for(local_root: str, relative_path: str) -> Optional[str]:
    """The local path mirroring ``relative_path`` under ``local_root``, or None if it would leave it."""
    base = os.path.realpath(local_root)
    target = os.path.realpath(os.path.join(base, relative_path))
    if target == base or not target.startswith(base + os.sep):
        return None
    return target


def resolve_reference(reference, referrer_dir: str = "", percent_encoded: bool = False) -> Optional[str]:
    """The root-relative path ``reference`` names, or None when it is not an accepted reference.

    ``referrer_dir`` is the referring file's directory relative to the confinement root. A
    percent-encoded reference (a glTF URI) is decoded first, so the check applies to the name that is
    downloaded rather than to its encoded spelling. Backslashes are read as separators (OBJ/MTL and
    FBX files written on Windows use them) before the path is checked.
    """
    if not isinstance(reference, str):
        return None
    value = unquote(reference) if percent_encoded else reference
    value = value.strip()
    if not value or len(value) > MAX_REFERENCE_LENGTH:
        return None
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        return None
    if value.startswith("`") or _SCHEME.match(value):
        return None
    value = value.replace("\\", "/")
    if value.startswith("/"):
        return None
    joined = posixpath.normpath(posixpath.join(referrer_dir or "", value))
    if joined in (".", "..") or joined.startswith(("/", "../")):
        return None
    return joined


# --------------------------------------------------------------------------------------------------
# Reference collectors: (local file) -> [(raw reference, percent_encoded)]
# --------------------------------------------------------------------------------------------------

def _gltf_references(path: str) -> List[tuple]:
    with open(path, "r", encoding="utf-8") as handle:
        document = json.load(handle)
    references = []
    for group in ("buffers", "images"):
        for entry in document.get(group) or []:
            uri = entry.get("uri") if isinstance(entry, dict) else None
            if isinstance(uri, str) and uri and not uri.startswith("data:"):
                references.append((uri, True))
    return references


def _obj_references(path: str) -> List[tuple]:
    """``mtllib`` names: one or more file names after the keyword."""
    references = []
    with open(path, "rb") as handle:
        for raw in handle:
            line = raw.strip()
            if line[:6].lower() != b"mtllib" or (len(line) > 6 and not line[6:7].isspace()):
                continue
            names = line[6:].decode("utf-8", errors="replace").split()
            references.extend((name, False) for name in names)
    return references


# MTL texture statements and the number of values each option takes (the -o/-s/-t options take one
# to three numbers, handled separately).
_MTL_MAP_KEYWORDS = {
    "map_kd", "map_ka", "map_ks", "map_ns", "map_d", "map_bump", "bump", "disp", "decal", "refl",
    "norm", "map_pr", "map_pm", "map_ps", "map_ke", "map_rma", "map_orm",
}
_MTL_OPTION_ARGS = {
    "-blendu": 1, "-blendv": 1, "-bm": 1, "-boost": 1, "-cc": 1, "-clamp": 1, "-imfchan": 1,
    "-mm": 2, "-texres": 1, "-type": 1,
}
_NUMBER = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


def _mtl_texture_name(tokens: List[str]) -> Optional[str]:
    index = 0
    while index < len(tokens):
        token = tokens[index].lower()
        if token in _MTL_OPTION_ARGS:
            index += 1 + _MTL_OPTION_ARGS[token]
        elif token in ("-o", "-s", "-t"):
            index += 1
            taken = 0
            while index < len(tokens) - 1 and taken < 3 and _NUMBER.match(tokens[index]):
                index += 1
                taken += 1
        else:
            break
    name = " ".join(tokens[index:])
    return name or None


def _mtl_references(path: str) -> List[tuple]:
    references = []
    with open(path, "rb") as handle:
        for raw in handle:
            tokens = raw.decode("utf-8", errors="replace").split()
            if len(tokens) >= 2 and tokens[0].lower() in _MTL_MAP_KEYWORDS:
                name = _mtl_texture_name(tokens[1:])
                if name:
                    references.append((name, False))
    return references


def _usd_references(path: str) -> List[tuple]:
    """Sublayers, references, payloads and asset-valued attribute paths authored in one layer."""
    from pxr import UsdUtils

    sublayers, references, payloads = UsdUtils.ExtractExternalReferences(path)
    found = []
    for value in list(sublayers) + list(references) + list(payloads):
        value = str(value)
        if "<UDIM>" in value.upper():
            logger.info(f"  Not fetching UDIM texture set: {value}")
            continue
        # A package-relative path names a file inside a .usdz: fetch the package itself.
        if "[" in value:
            value = value.split("[", 1)[0]
        found.append((value, False))
    return found


_FBX_TEXTURE = re.compile(
    rb"[A-Za-z0-9_ .:/\\\-()]{1,512}\.(?:png|jpe?g|tga|bmp|tiff?|dds|exr|hdr|psd|gif)",
    re.IGNORECASE)
_FBX_SCAN_LIMIT = 512 * 1024 ** 2
# An LCC scene description is a small JSON document; more than this is not one.
_JSON_SCAN_LIMIT = 64 * 1024 ** 2


def _fbx_references(path: str) -> List[tuple]:
    """Texture file names stored in an FBX file (binary or ASCII), by scanning for image names.

    FBX keeps a texture's absolute ``FileName`` from the authoring machine and its ``RelativeFilename``;
    both are offered and the resolver discards the absolute one. The scan reads at most the first
    512 MiB.
    """
    with open(path, "rb") as handle:
        data = handle.read(_FBX_SCAN_LIMIT)
    names = []
    for match in _FBX_TEXTURE.finditer(data):
        name = match.group(0).decode("utf-8", errors="replace").strip()
        if name not in names:
            names.append(name)
    references = [(name, False) for name in names]
    # An authoring machine's absolute path keeps the file name as its last segment, and a binary
    # string record's length bytes can read as text and prefix the match; the bare file name is
    # offered too, as a sibling of the FBX -- where an uploader most often puts the textures.
    for name in names:
        base = name.replace("\\", "/").rsplit("/", 1)[-1]
        if (base, False) not in references:
            references.append((base, False))
    return references


# The data files an XGRIDS LCC scene (``meta.lcc``) reads beside it; ``environment`` may name another.
_LCC_SIBLINGS = ("data.bin", "index.bin", "shcoef.bin", "environment.bin")


def _lcc_references(path: str) -> List[tuple]:
    """The data files beside an LCC scene description, plus the environment file its JSON names."""
    references = [(name, False) for name in _LCC_SIBLINGS]
    try:
        with open(path, "rb") as handle:
            meta = json.loads(handle.read(_JSON_SCAN_LIMIT).decode("utf-8", errors="replace"))
        environment = meta.get("environment") if isinstance(meta, dict) else None
        if isinstance(environment, str) and (environment, False) not in references:
            references.append((environment, False))
    except (OSError, ValueError):
        pass
    return references


_COLLECTORS: Dict[str, Callable[[str], List[tuple]]] = {
    ".gltf": _gltf_references,
    ".obj": _obj_references,
    ".mtl": _mtl_references,
    ".fbx": _fbx_references,
    ".lcc": _lcc_references,
    **{ext: _usd_references for ext in _USD_LAYER_EXTENSIONS},
}


def has_references(ext: str) -> bool:
    """True when files of this extension can reference other files the container fetches."""
    return ext.lower() in _COLLECTORS


# --------------------------------------------------------------------------------------------------
# Fetch
# --------------------------------------------------------------------------------------------------

def fetch_referenced_files(bucket: str, input_key: str, local_input_path: str, local_root: str,
                           root_key: str, ext: str, limits: Optional[FetchLimits] = None) -> FetchReport:
    """Fetch every accepted file the input references, transitively, into ``local_root``.

    ``local_input_path`` is the input already downloaded at its root-relative path under
    ``local_root``; each fetched file lands at its own root-relative path beside it, so relative
    references resolve locally exactly as they do in the asset.
    """
    limits = limits or FetchLimits()
    report = FetchReport()
    if not has_references(ext):
        return report

    input_rel = relative_to_root(input_key, root_key)
    seen = {input_rel}
    queue = deque([(local_input_path, input_rel, ext.lower(), 0)])
    budget_logged = False

    while queue:
        local_path, rel_path, file_ext, depth = queue.popleft()
        collector = _COLLECTORS.get(file_ext)
        if collector is None:
            continue
        try:
            references = collector(local_path)
        except Exception as e:
            logger.warning(f"Could not read the references in {rel_path}: {e}")
            continue
        referrer_dir = posixpath.dirname(rel_path)
        for raw, percent_encoded in references:
            relative = resolve_reference(raw, referrer_dir, percent_encoded=percent_encoded)
            target = local_path_for(local_root, relative) if relative else None
            if relative is None or target is None:
                logger.warning(f"  Skipping reference in {rel_path}: not a path inside the asset: "
                               f"{raw!r}")
                report.rejected.append(raw)
                continue
            if relative in seen:
                continue
            seen.add(relative)
            if len(report.fetched) >= limits.max_files:
                if not budget_logged:
                    logger.warning(f"  Referenced-file limit reached ({limits.max_files} files); "
                                   f"further references are skipped")
                    budget_logged = True
                report.skipped_for_budget.append(relative)
                continue
            key = root_key + relative
            size = s3.head_object_size(bucket, key)
            if size is None:
                logger.warning(f"  Referenced file not found in the asset: {relative}")
                report.missing.append(relative)
                continue
            if report.total_bytes + size > limits.max_total_bytes:
                logger.warning(f"  Skipping {relative} ({size} bytes): referenced files would exceed "
                               f"{limits.max_total_bytes} bytes")
                report.skipped_for_budget.append(relative)
                continue
            free = shutil.disk_usage(local_root).free
            if size + limits.disk_headroom_bytes > free:
                logger.warning(f"  Skipping {relative} ({size} bytes): {free} bytes of disk free")
                report.skipped_for_budget.append(relative)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if s3.download(bucket, key, target) is None:
                report.missing.append(relative)
                continue
            report.fetched.append(relative)
            report.total_bytes += size
            child_ext = os.path.splitext(relative)[1].lower()
            if child_ext in _COLLECTORS and child_ext != ".gltf":
                if depth + 1 <= limits.max_depth:
                    queue.append((target, relative, child_ext, depth + 1))
                else:
                    logger.warning(f"  Not following references in {relative}: depth limit "
                                   f"{limits.max_depth} reached")

    logger.info(f"Referenced files: {len(report.fetched)} fetched ({report.total_bytes} bytes), "
                f"{len(report.rejected)} rejected, {len(report.missing)} missing, "
                f"{len(report.skipped_for_budget)} skipped for the budget")
    return report

