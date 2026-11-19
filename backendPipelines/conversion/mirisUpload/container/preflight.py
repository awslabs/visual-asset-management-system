#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""Texture format pre-flight for the files an upload holds, and the run's warnings report.

Each non-layer file in the root's dependency tree, and each non-layer member of a .usdz the tree
holds, is classified by its extension and its first bytes. PNG, JPEG and EXR pass. WebP, by either,
fails the run. Any other format, and a file whose bytes do not match its extension, is a warning.
"""
import os
import zipfile
from typing import Iterable, List, Tuple

ALLOWED_FORMATS = ("png", "jpeg", "exr")
_EXTENSION_FORMATS = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".exr": "exr", ".webp": "webp"}
# Members of a package that are layers, not textures.
_LAYER_EXTENSIONS = (".usd", ".usda", ".usdc", ".usdz")
_HEAD_BYTES = 12

# The report lists at most this many warnings, in at most this many bytes.
REPORT_MAX_WARNINGS = 50
REPORT_MAX_BYTES = 4096


def content_format(head: bytes) -> str:
    """'png', 'jpeg', 'exr' or 'webp' from a file's first bytes; '' for anything else."""
    if head.startswith(b"\x89PNG"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x76\x2f\x31\x01"):
        return "exr"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return ""


def _package_path(identifier: str) -> str:
    if identifier.endswith("]") and "[" in identifier:
        return identifier[: identifier.index("[")]
    return identifier


def _is_layer(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in _LAYER_EXTENSIONS


def upload_files(paths: Iterable[str], base_dir: str) -> List[Tuple[str, bytes]]:
    """(asset-relative name, first bytes) of each non-layer file among `paths` (layer identifiers
    and asset paths) and of each non-layer member of a .usdz among them, named 'pkg.usdz[member]'.
    A file that is not on disk, and a package that is not a readable zip, is left out."""
    base = os.path.realpath(base_dir)
    found = {}
    for path in paths:
        real = os.path.realpath(_package_path(path))
        if not os.path.isfile(real):
            continue
        rel = os.path.relpath(real, base).replace(os.sep, "/")
        if real.lower().endswith(".usdz"):
            if rel in found:
                continue
            found[rel] = None
            try:
                with zipfile.ZipFile(real) as package:
                    for member in package.infolist():
                        if member.is_dir() or _is_layer(member.filename):
                            continue
                        with package.open(member) as handle:
                            found[f"{rel}[{member.filename}]"] = handle.read(_HEAD_BYTES)
            except (zipfile.BadZipFile, OSError):
                pass
        elif not _is_layer(real) and rel not in found:
            with open(real, "rb") as handle:
                found[rel] = handle.read(_HEAD_BYTES)
    return sorted((name, head) for name, head in found.items() if head is not None)


def _member_name(name: str) -> str:
    return name[name.index("[") + 1:-1] if name.endswith("]") and "[" in name else name


def classify(files: Iterable[Tuple[str, bytes]]) -> Tuple[List[str], List[str]]:
    """(WebP files, warnings) for `files` ((name, first bytes) pairs). A file is WebP when its
    extension or its bytes say so; any other file that is not PNG, JPEG or EXR by both is a warning."""
    webp, warnings = [], []
    for name, head in files:
        extension = os.path.splitext(_member_name(name))[1].lower()
        expected = _EXTENSION_FORMATS.get(extension, "")
        actual = content_format(head)
        if "webp" in (expected, actual):
            webp.append(name)
        elif expected not in ALLOWED_FORMATS:
            warnings.append(f"{name}: {extension or 'a file without an extension'} is not PNG, JPEG or EXR")
        elif actual != expected:
            warnings.append(f"{name}: its contents are not {expected.upper()}, as its extension says")
    return webp, warnings


def report_text(root: str, warnings: List[str]) -> str:
    """The warnings report: a heading and one line per warning, at most REPORT_MAX_WARNINGS lines
    and REPORT_MAX_BYTES bytes of UTF-8, with a closing line counting any left out."""
    heading = f"Miris upload pre-flight warnings for {root} ({len(warnings)}):\n"
    lines, size = [], len(heading.encode("utf-8"))
    for warning in warnings[:REPORT_MAX_WARNINGS]:
        line = f"- {warning[:300]}\n"
        if size + len(line.encode("utf-8")) > REPORT_MAX_BYTES - 64:
            break
        lines.append(line)
        size += len(line.encode("utf-8"))
    left_out = len(warnings) - len(lines)
    tail = f"... and {left_out} more; the container log lists them.\n" if left_out else ""
    return heading + "".join(lines) + tail
