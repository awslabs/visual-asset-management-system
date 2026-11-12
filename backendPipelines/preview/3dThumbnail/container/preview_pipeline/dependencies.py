# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Referenced files: fetch the files an input names (glTF buffers and images, USD sublayers, references,
payloads and asset-valued attributes, OBJ material libraries and their texture maps, FBX texture
files, an XGRIDS ``.lcc`` scene's data files and the chunk files of one ``.lcc2`` level of detail) from
the input's own asset, so the loader opens the input against them.

Every reference is untrusted input. The container's job role can read every asset bucket, so a
reference must not pull another asset's content into this thumbnail (ARCC BSC1, input validation by
allowlist). A reference is accepted only when it is a relative path that, normalised against the
referring file's directory, stays inside the input's asset prefix; absolute paths, ``..`` escapes,
URI schemes (``s3:``, ``http(s):``, ``file:``, ``anon:``, a Windows drive), control characters and
over-long values are rejected. The fetch is bounded by a file count, a byte total (each object's size
is read with HeadObject before it is downloaded and checked against free disk) and a recursion depth
for formats whose fetched files reference further files. A rejected, missing or over-budget
reference is logged and skipped; it never fails the run. A run that skipped any is reported as an
incomplete preview (``IncompletePreview``): one summary in the job log that lists each skipped file with
its reason, separating the geometry layers a USD stage composes from textures, materials and data files.
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


# What a referenced file is to the preview, which decides how its absence is reported. A collector
# names the kind with each reference; one that names none is classified by the target's extension.
KIND_GEOMETRY_LAYER = "geometryLayer"   # a USD sublayer, reference or payload: geometry is missing
KIND_DATA_FILE = "dataFile"             # a glTF buffer, LCC data or chunk file: geometry/points missing
KIND_TEXTURE_OR_OTHER = "textureOrOther"  # a texture, material or other asset-valued attribute
# Asset paths that load nothing (a USD ``assetInfo`` identifier and other metadata): still fetched as
# before, but a missing one removes nothing from the preview, so it is not reported.
KIND_METADATA = "metadata"
# A material module the renderer resolves from its own search path (a bare ``OmniPBR.mdl``), not a
# file of the asset: still looked up as before, not reported.
KIND_RENDERER_MDL = "rendererMdl"
_UNREPORTED_KINDS = (KIND_METADATA, KIND_RENDERER_MDL)


@dataclass
class SkippedReference:
    """One referenced file that was not fetched: as the referring file names it (a rejected reference)
    or as its path inside the asset, the referring file's path, and why it was skipped."""
    reference: str
    reason: str
    referrer: str = ""
    # True for a reference refused as not a path inside the asset: ``reference`` is then as authored.
    rejected: bool = False
    # What the file is to the preview (KIND_*); empty classifies by the target's extension.
    kind: str = ""
    # True when ``reference`` is a percent-encoded URI (glTF).
    percent_encoded: bool = False

    @property
    def resolved_kind(self) -> str:
        if self.kind:
            return self.kind
        return KIND_GEOMETRY_LAYER if is_geometry_layer(self.reference) else KIND_TEXTURE_OR_OTHER

    @property
    def is_geometry_layer(self) -> bool:
        return self.resolved_kind == KIND_GEOMETRY_LAYER


@dataclass
class FetchReport:
    fetched: List[str] = field(default_factory=list)
    rejected: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    skipped_for_budget: List[str] = field(default_factory=list)
    total_bytes: int = 0
    # Every entry of rejected, missing and skipped_for_budget, in fetch order, with its reason.
    skipped: List[SkippedReference] = field(default_factory=list)
    # Fetched files whose own references were not read (unreadable, or past the depth limit), with why.
    not_followed: List[SkippedReference] = field(default_factory=list)


# USD layer files, for a reference whose collector named no kind.
_GEOMETRY_LAYER_EXTENSIONS = _USD_LAYER_EXTENSIONS | {".usdz"}


def is_geometry_layer(reference: str) -> bool:
    """True when ``reference`` names a USD layer file by its extension. Used only for a reference whose
    collector named no kind; a USD layer's own references are classified by their composition arc."""
    if not isinstance(reference, str):
        return False
    path = reference.strip().replace("\\", "/").split("[", 1)[0].split("?", 1)[0].split("#", 1)[0]
    return posixpath.splitext(path.rstrip("/"))[1].lower() in _GEOMETRY_LAYER_EXTENSIONS


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


_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


def rejection_reason(reference, percent_encoded: bool = False) -> str:
    """Why ``resolve_reference`` refused ``reference``, for the incomplete-preview summary. It only
    explains a refusal; whether a reference is accepted is decided by ``resolve_reference`` alone."""
    if not isinstance(reference, str):
        return "not a file path"
    value = (unquote(reference) if percent_encoded else reference).strip()
    if not value:
        return "an empty path"
    if len(value) > MAX_REFERENCE_LENGTH:
        return f"longer than {MAX_REFERENCE_LENGTH} characters"
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        return "contains control characters"
    if value.startswith("`"):
        return "an expression, not a file path"
    if _WINDOWS_DRIVE.match(value):
        return "outside the asset (absolute path)"
    if _SCHEME.match(value):
        return "outside the asset (URI scheme)"
    if value.replace("\\", "/").startswith("/"):
        return "outside the asset (absolute path)"
    if ".." in value.replace("\\", "/").split("/"):
        return "outside the asset ('..' escape)"
    return "outside the asset"


# --------------------------------------------------------------------------------------------------
# Reference collectors: (local file) -> [(raw reference, percent_encoded)]
# --------------------------------------------------------------------------------------------------

def _gltf_references(path: str) -> List[tuple]:
    with open(path, "r", encoding="utf-8") as handle:
        document = json.load(handle)
    references = []
    for group, kind in (("buffers", KIND_DATA_FILE), ("images", KIND_TEXTURE_OR_OTHER)):
        for entry in document.get(group) or []:
            uri = entry.get("uri") if isinstance(entry, dict) else None
            if isinstance(uri, str) and uri and not uri.startswith("data:"):
                references.append((uri, True, kind))
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


def _asset_values(value) -> List[str]:
    """The asset paths an attribute value holds (an ``Sdf.AssetPath`` or an array of them)."""
    from pxr import Sdf

    if isinstance(value, Sdf.AssetPath):
        return [value.path]
    if isinstance(value, (str, bytes)) or not hasattr(value, "__iter__"):
        return []
    try:
        return [item.path for item in value if isinstance(item, Sdf.AssetPath)]
    except TypeError:
        return []


def _usd_reference_kinds(path: str) -> Optional[tuple]:
    """(composition arcs, asset-valued attribute paths) authored in one layer, as authored, or None
    when the layer cannot be read for it. Anything else ``ExtractExternalReferences`` returns is
    metadata (an ``assetInfo`` identifier, say), which loads nothing."""
    from pxr import Sdf

    try:
        layer = Sdf.Layer.FindOrOpen(path)
        if layer is None:
            return None
        composition = {str(value) for value in layer.GetCompositionAssetDependencies()}
        attribute_assets = set()

        def visit(spec_path):
            spec = layer.GetObjectAtPath(spec_path)
            if not isinstance(spec, Sdf.AttributeSpec):
                return
            if spec.HasDefaultValue():
                attribute_assets.update(_asset_values(spec.default))
            for time in layer.ListTimeSamplesForPath(spec_path):
                attribute_assets.update(_asset_values(layer.QueryTimeSample(spec_path, time)))

        layer.Traverse(Sdf.Path.absoluteRootPath, visit)
        return composition, attribute_assets
    except Exception as e:
        logger.info(f"  Could not classify the references in {os.path.basename(path)} "
                    f"({type(e).__name__}); classifying them by extension")
        return None


def _usd_references(path: str) -> List[tuple]:
    """Sublayers, references, payloads and asset-valued attribute paths authored in one layer, each
    with its kind: a composition arc is a geometry layer, an asset-valued attribute (a texture) is a
    texture or other file, and an asset path in metadata (an ``assetInfo`` identifier) loads nothing.
    Which references are fetched does not depend on the kind."""
    from pxr import UsdUtils

    sublayers, references, payloads = UsdUtils.ExtractExternalReferences(path)
    kinds = _usd_reference_kinds(path)
    found = []
    for arc, values in (("sublayer", sublayers), ("reference", references), ("payload", payloads)):
        for value in values:
            value = str(value)
            if arc != "reference":
                kind = KIND_GEOMETRY_LAYER
            elif kinds is None:
                kind = ""
            elif value in kinds[0]:
                kind = KIND_GEOMETRY_LAYER
            elif value in kinds[1]:
                kind = KIND_TEXTURE_OR_OTHER
            else:
                kind = KIND_METADATA
            if "<UDIM>" in value.upper():
                logger.info(f"  Not fetching UDIM texture set: {value}")
                continue
            # A package-relative path names a file inside a .usdz: fetch the package itself.
            if "[" in value:
                value = value.split("[", 1)[0]
            found.append((value, False, kind))
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
    references = [(name, False, KIND_DATA_FILE) for name in _LCC_SIBLINGS]
    try:
        with open(path, "rb") as handle:
            meta = json.loads(handle.read(_JSON_SCAN_LIMIT).decode("utf-8", errors="replace"))
        environment = meta.get("environment") if isinstance(meta, dict) else None
        if isinstance(environment, str) and (environment, False, KIND_DATA_FILE) not in references:
            references.append((environment, False, KIND_DATA_FILE))
    except (OSError, ValueError):
        pass
    return references


def _lcc2_references(path: str, max_splats: Optional[int] = None) -> List[tuple]:
    """The chunk files of the one level of detail an ``.lcc2`` scene is read at (``lcc_meta``).

    The other levels and the environment chunk are not read for a thumbnail, so they are not fetched.
    """
    from .format_handlers import lcc_meta

    meta = lcc_meta.read_meta(path)
    _, names = lcc_meta.lcc2_level_files(meta, max_splats or lcc_meta.DEFAULT_MAX_SPLATS)
    return [(name, False, KIND_DATA_FILE) for name in names]


_COLLECTORS: Dict[str, Callable[[str], List[tuple]]] = {
    ".gltf": _gltf_references,
    ".obj": _obj_references,
    ".mtl": _mtl_references,
    ".fbx": _fbx_references,
    ".lcc": _lcc_references,
    ".lcc2": _lcc2_references,
    **{ext: _usd_references for ext in _USD_LAYER_EXTENSIONS},
}
# Collectors whose references depend on the splat budget.
_BUDGETED_COLLECTORS = {".lcc2"}


def has_references(ext: str) -> bool:
    """True when files of this extension can reference other files the container fetches."""
    return ext.lower() in _COLLECTORS


def _is_renderer_mdl(reference) -> bool:
    """True for a ``.mdl`` reference with no folder component: a material module the renderer resolves
    from its own MDL search path, not a file next to the layer."""
    if not isinstance(reference, str):
        return False
    value = reference.strip()
    return value.lower().endswith(".mdl") and "/" not in value and "\\" not in value \
        and not _SCHEME.match(value)


# --------------------------------------------------------------------------------------------------
# Fetch
# --------------------------------------------------------------------------------------------------

def fetch_referenced_files(bucket: str, input_key: str, local_input_path: str, local_root: str,
                           root_key: str, ext: str, limits: Optional[FetchLimits] = None,
                           max_splats: Optional[int] = None,
                           report: Optional[FetchReport] = None) -> FetchReport:
    """Fetch every accepted file the input references, transitively, into ``local_root``.

    ``local_input_path`` is the input already downloaded at its root-relative path under
    ``local_root``; each fetched file lands at its own root-relative path beside it, so relative
    references resolve locally exactly as they do in the asset. ``max_splats`` is the splat budget an
    ``.lcc2`` scene's level of detail is chosen for. A caller-supplied ``report`` is filled in place,
    so what was recorded before an unexpected error is still there when the call raises.
    """
    limits = limits or FetchLimits()
    report = report if report is not None else FetchReport()
    if not has_references(ext):
        return report

    input_rel = relative_to_root(input_key, root_key)
    seen = {input_rel}
    queue = deque([(local_input_path, input_rel, ext.lower(), 0)])
    budget_logged = False

    mdl_logged = set()

    def skip(bucket_list: List[str], name: str, reason: str, referrer: str, kind: str = "",
             percent_encoded: bool = False):
        bucket_list.append(name)
        report.skipped.append(SkippedReference(name, reason, referrer,
                                               rejected=bucket_list is report.rejected, kind=kind,
                                               percent_encoded=percent_encoded))

    while queue:
        local_path, rel_path, file_ext, depth = queue.popleft()
        collector = _COLLECTORS.get(file_ext)
        if collector is None:
            continue
        try:
            if file_ext in _BUDGETED_COLLECTORS:
                references = collector(local_path, max_splats=max_splats)
            else:
                references = collector(local_path)
        except Exception as e:
            logger.warning(f"Could not read the references in {rel_path}: {e}")
            report.not_followed.append(SkippedReference(
                rel_path, f"its references could not be read ({type(e).__name__})"))
            continue
        referrer_dir = posixpath.dirname(rel_path)
        for entry in references:
            raw, percent_encoded = entry[0], entry[1]
            kind = entry[2] if len(entry) > 2 else ""
            if _is_renderer_mdl(raw):
                kind = KIND_RENDERER_MDL
            relative = resolve_reference(raw, referrer_dir, percent_encoded=percent_encoded)
            target = local_path_for(local_root, relative) if relative else None
            if relative is None or target is None:
                logger.warning(f"  Skipping reference in {rel_path}: not a path inside the asset: "
                               f"{raw!r}")
                reason = (rejection_reason(raw, percent_encoded) if relative is None
                          else "outside the asset")
                skip(report.rejected, raw, reason, rel_path, kind, percent_encoded)
                continue
            if relative in seen:
                continue
            seen.add(relative)
            if len(report.fetched) >= limits.max_files:
                if not budget_logged:
                    logger.warning(f"  Referenced-file limit reached ({limits.max_files} files); "
                                   f"further references are skipped")
                    budget_logged = True
                skip(report.skipped_for_budget, relative,
                     f"over the file budget ({limits.max_files} files)", rel_path, kind)
                continue
            key = root_key + relative
            size = s3.head_object_size(bucket, key)
            if size is None:
                if kind == KIND_RENDERER_MDL:
                    if raw not in mdl_logged:
                        mdl_logged.add(raw)
                        logger.info(f"  MDL module resolved by the renderer, not fetched: {raw!r}")
                else:
                    logger.warning(f"  Referenced file not found in the asset: {relative}")
                skip(report.missing, relative, "missing from the asset", rel_path, kind)
                continue
            if report.total_bytes + size > limits.max_total_bytes:
                logger.warning(f"  Skipping {relative} ({size} bytes): referenced files would exceed "
                               f"{limits.max_total_bytes} bytes")
                skip(report.skipped_for_budget, relative,
                     f"over the byte budget ({limits.max_total_bytes} bytes)", rel_path, kind)
                continue
            free = shutil.disk_usage(local_root).free
            if size + limits.disk_headroom_bytes > free:
                logger.warning(f"  Skipping {relative} ({size} bytes): {free} bytes of disk free")
                skip(report.skipped_for_budget, relative,
                     f"over the disk budget ({size} bytes, {free} bytes free)", rel_path, kind)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if s3.download(bucket, key, target) is None:
                skip(report.missing, relative, "could not be downloaded from the asset", rel_path,
                     kind)
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
                    report.not_followed.append(SkippedReference(
                        relative, f"its references were not read (depth limit {limits.max_depth})"))

    logger.info(f"Referenced files: {len(report.fetched)} fetched ({report.total_bytes} bytes), "
                f"{len(report.rejected)} rejected, {len(report.missing)} missing, "
                f"{len(report.skipped_for_budget)} skipped for the budget")
    return report


# --------------------------------------------------------------------------------------------------
# Incomplete preview
# --------------------------------------------------------------------------------------------------

# Entries listed per group in the summary; a scene that names thousands of missing files keeps its
# summary bounded, with the count of the rest.
MAX_SUMMARY_ENTRIES = 200


@dataclass
class IncompletePreview:
    """What a run's preview is missing because referenced files were not fetched."""
    geometry_layers: List[SkippedReference]
    other_files: List[SkippedReference]
    not_followed: List[SkippedReference]
    fetch_error: str = ""
    # glTF buffers and LCC data or chunk files: their absence drops geometry or points.
    data_files: List[SkippedReference] = field(default_factory=list)

    @property
    def skipped_count(self) -> int:
        return len(self.geometry_layers) + len(self.data_files) + len(self.other_files)

    def summary(self) -> str:
        """What was not fetched, as one sentence without a verdict on the preview."""
        if not self.skipped_count:
            return "not every referenced file could be read"
        geometry = len(self.geometry_layers)
        data = len(self.data_files)
        other = len(self.other_files)
        return (f"{self.skipped_count} referenced {_plural(self.skipped_count, 'file', 'files')} "
                f"could not be fetched ({geometry} geometry {_plural(geometry, 'layer', 'layers')}, "
                f"{data} data {_plural(data, 'file', 'files')}, "
                f"{other} {_plural(other, 'texture/other file', 'textures/other files')})")

    def message(self) -> str:
        """The run's completion message, once the preview has been generated."""
        if self.skipped_count:
            return f"Preview generated, but incomplete: {self.summary()}. See the job log for the list."
        return ("Preview generated, but it may be incomplete: not every referenced file could be "
                "read. See the job log for the details.")

    def log(self):
        """One WARNING summary, logged after the fetch and before the preview is rendered: the count,
        then each skipped file with its reason, geometry layers first. Every listed name is quoted with
        repr, so a reference cannot forge a log line."""
        logger.warning(f"Incomplete preview: {self.summary()}")
        _log_group("Geometry layers not fetched (their geometry is missing from the preview)",
                   self.geometry_layers)
        _log_group("Data files not fetched (geometry buffers or point data: geometry or points are "
                   "missing from the preview)", self.data_files)
        _log_group("Textures, materials and other files not fetched (colour or detail may be missing "
                   "from the preview)", self.other_files)
        _log_group("Fetched files whose own references were not followed", self.not_followed)
        if self.fetch_error:
            logger.warning(f"  The referenced-file fetch stopped early ({self.fetch_error}); "
                           f"files it had not reached were not fetched.")


def _plural(count: int, one: str, many: str) -> str:
    return one if count == 1 else many


def _log_group(title: str, entries: List[SkippedReference]):
    if not entries:
        return
    logger.warning(f"  {title}: {len(entries)}")
    for entry in entries[:MAX_SUMMARY_ENTRIES]:
        where = f" (named in {entry.referrer!r})" if entry.referrer else ""
        logger.warning(f"    {entry.reference!r}{where}: {entry.reason}")
    if len(entries) > MAX_SUMMARY_ENTRIES:
        logger.warning(f"    ... and {len(entries) - MAX_SUMMARY_ENTRIES} more")


def incomplete_preview(report: Optional[FetchReport],
                       fetch_error: Optional[BaseException] = None) -> Optional[IncompletePreview]:
    """The incomplete-preview summary of a fetch, or None when every referenced file was fetched.

    A reference skipped more than once (the same name in several layers) is counted once per reason.
    A reference that loads nothing (USD metadata) or that the renderer resolves itself (a bare ``.mdl``
    module) is not counted. ``fetch_error`` is the exception that ended the fetch early, if one did.
    """
    report = report or FetchReport()
    unique: List[SkippedReference] = []
    keys = set()
    for entry in report.skipped:
        if entry.resolved_kind in _UNREPORTED_KINDS:
            continue
        key = (entry.reference, entry.reason)
        if key not in keys:
            keys.add(key)
            unique.append(entry)
    if not unique and not report.not_followed and fetch_error is None:
        return None
    return IncompletePreview(
        geometry_layers=[e for e in unique if e.resolved_kind == KIND_GEOMETRY_LAYER],
        data_files=[e for e in unique if e.resolved_kind == KIND_DATA_FILE],
        other_files=[e for e in unique if e.resolved_kind == KIND_TEXTURE_OR_OTHER],
        not_followed=list(report.not_followed),
        fetch_error=type(fetch_error).__name__ if fetch_error is not None else "",
    )


# --------------------------------------------------------------------------------------------------
# Incomplete-preview result document
# --------------------------------------------------------------------------------------------------

# The object an incomplete run writes under the execution's results folder. The workflow's output step
# records every object there as a results row on the execution, so it shows under Output Results.
INCOMPLETE_RESULT_FILENAME = "preview-incomplete.json"
INCOMPLETE_RESULT_SCHEMA = "vams.preview3dThumbnail.incomplete/v1"
# Entries listed in the document, across every kind; the counts are always complete.
MAX_RESULT_ENTRIES = 100
# Characters kept of any one path or reason.
MAX_RESULT_TEXT_CHARS = 256
# Upper bound of the encoded document, well inside the results row's text budget.
MAX_RESULT_DOCUMENT_BYTES = 64 * 1024
# A refused reference is named in the document only by a base file name of one of these types; any
# other value (a bucket, a host, a share, a user folder) is left out and only the reason is written.
_SAFE_RESULT_EXTENSIONS = {
    ".usd", ".usda", ".usdc", ".usdz", ".gltf", ".glb", ".bin", ".obj", ".mtl", ".fbx", ".mdl",
    ".png", ".jpg", ".jpeg", ".tga", ".bmp", ".tif", ".tiff", ".dds", ".exr", ".hdr", ".psd", ".gif",
    ".ktx", ".ktx2", ".webp", ".lcc", ".lcc2", ".spz", ".sog", ".ply", ".splat", ".ksplat", ".vdb",
    ".abc",
}
_SAFE_BASE_NAME = re.compile(r"^[A-Za-z0-9 _.()+\-]{1,128}$")


def _bounded(text: str) -> str:
    text = text if isinstance(text, str) else str(text)
    return text if len(text) <= MAX_RESULT_TEXT_CHARS else text[:MAX_RESULT_TEXT_CHARS - 3] + "..."


def safe_base_name(reference) -> str:
    """The base file name of an as-authored reference with every location part removed, or "" when no
    safe file name is left. Percent-encoding is decoded first; then the URI scheme and authority
    (``s3://bucket/``, ``http://host:port/``, ``omniverse://host/``), a Windows drive or UNC server,
    the query, the fragment and any package path are dropped, and what remains must be a plain file
    name of a known file type."""
    if not isinstance(reference, str):
        return ""
    value = reference.strip()
    for _ in range(3):  # a doubly encoded value decodes in two passes
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded
    value = value.replace("\\", "/").split("?", 1)[0].split("#", 1)[0].split("[", 1)[0]
    scheme = _SCHEME.match(value)
    if scheme:
        value = value[scheme.end():]
    if value.startswith("//"):
        authority_and_path = value[2:]
        value = authority_and_path.split("/", 1)[1] if "/" in authority_and_path else ""
    if not value or value.endswith("/"):
        return ""
    base = value.rsplit("/", 1)[-1]
    if not _SAFE_BASE_NAME.match(base) or base.startswith("."):
        return ""
    if posixpath.splitext(base)[1].lower() not in _SAFE_RESULT_EXTENSIONS:
        return ""
    return base


def _result_path(entry: SkippedReference) -> str:
    """The path the document names: the asset-relative path of a file the fetch looked up, or at most a
    safe base file name of a reference refused as outside the asset (``safe_base_name``)."""
    if not entry.rejected:
        return _bounded(entry.reference)
    return safe_base_name(entry.reference)


def incomplete_result_document(incomplete: IncompletePreview, input_path: str = "") -> bytes:
    """The result document of an incomplete preview, as UTF-8 JSON bytes of at most
    MAX_RESULT_DOCUMENT_BYTES: the completion message, the counts by kind, and up to MAX_RESULT_ENTRIES
    skipped files (geometry layers first), with ``truncated`` set when any were left out. Paths are
    relative to the input's asset; no bucket name, host, S3 key or full URI is written."""
    rows = []
    for kind, entries in ((KIND_GEOMETRY_LAYER, incomplete.geometry_layers),
                          (KIND_DATA_FILE, incomplete.data_files),
                          (KIND_TEXTURE_OR_OTHER, incomplete.other_files),
                          ("notFollowed", incomplete.not_followed)):
        for entry in entries:
            rows.append({
                "kind": kind,
                "path": _result_path(entry),
                "namedIn": _bounded(entry.referrer),
                "reason": _bounded(entry.reason),
            })
    document = {
        "schema": INCOMPLETE_RESULT_SCHEMA,
        "status": "INCOMPLETE",
        "message": incomplete.message(),
        "inputFile": _bounded(input_path),
        "counts": {
            "skipped": incomplete.skipped_count,
            "geometryLayers": len(incomplete.geometry_layers),
            "dataFiles": len(incomplete.data_files),
            "texturesOrOther": len(incomplete.other_files),
            "notFollowed": len(incomplete.not_followed),
        },
        "fetchStoppedEarly": bool(incomplete.fetch_error),
        "skippedFiles": rows[:MAX_RESULT_ENTRIES],
        "truncated": len(rows) > MAX_RESULT_ENTRIES,
    }
    body = json.dumps(document, indent=2).encode("utf-8")
    while len(body) > MAX_RESULT_DOCUMENT_BYTES and document["skippedFiles"]:
        document["skippedFiles"] = document["skippedFiles"][:len(document["skippedFiles"]) // 2]
        document["truncated"] = True
        body = json.dumps(document, indent=2).encode("utf-8")
    return body
