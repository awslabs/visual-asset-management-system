# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The `other` class: a file whose extension names no class. The bytes are probed for a format this image
reads and the matching extractor runs, so a `.bak` that is a JPEG is analysed as an image and a `.dat` that is
an MP4 as a video — the result's file class says what was found. A zip or tar container is described under
`sys_archive` (entry count, uncompressed size, the leading entry names and, for an OPC package, its core
properties); readable text takes the text extractor. A file no probe recognises keeps `other` with the
constructPipeline `sys_file` facts (size, content type, last-modified time, S3 metadata, detected format).

The probes run in order and the first that recognises the file decides: Pillow, then an ffmpeg header read
(a video stream -> video, audio only -> audio), then the archive formats, then a text decode."""

import os
import subprocess
import tarfile
import xml.etree.ElementTree as ElementTree
import zipfile
from typing import Callable, Dict, List, Optional

from PIL import Image, UnidentifiedImageError

from . import audio, images, text, video
from .common import (
    ARCHIVE_MAX_LISTED_ENTRIES,
    CLASS_AUDIO,
    CLASS_OTHER,
    BranchResult,
    ExtractContext,
    bounded_tags,
    other_fallback,
)

_TEXT_SAMPLE_BYTES = 64 * 1024
_TEXT_CONTROL_BYTES = bytes(range(0x00, 0x09)) + bytes(range(0x0E, 0x20)) + b"\x7f"
# OPC packages (docx/xlsx/pptx and their templates, and any package built on the convention) keep their
# document properties here; the Dublin Core / core-properties element names are recorded as found.
_OPC_CORE_PART = "docProps/core.xml"
_OPC_APP_PART = "docProps/app.xml"


def _strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _xml_leaf_values(payload: bytes) -> List[tuple]:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        return []
    items = []
    for element in root:
        if list(element):
            continue
        value = (element.text or "").strip()
        if value:
            items.append((_strip_namespace(element.tag), value))
    return items


def _probe_image(path: str, ctx: ExtractContext) -> Optional[BranchResult]:
    try:
        with Image.open(path) as probe:
            probe.verify()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        return None
    except Exception:  # noqa: BLE001 - verify() raises format-specific errors; any means "not a usable image"
        return None
    result = images.extract_image(path, ctx)
    result.warnings.insert(0, f"{ctx.file_name}: extension {ctx.extension or '(none)'} is not an image "
                              f"extension; the bytes are a {result.attributes.get('sys_image', {}).get('format', 'raster image')}")
    return result


def _probe_media(path: str, ctx: ExtractContext, run: Callable = subprocess.run) -> Optional[BranchResult]:
    try:
        proc = video.run_ffmpeg(["-i", path, "-f", "null", "-"], run=run)
    except (OSError, subprocess.TimeoutExpired, RuntimeError):
        return None
    header = proc.stderr.decode("utf-8", "replace")
    facts = video.parse_ffmpeg_header_text(header)
    if not facts.get("streamCount"):
        return None
    if facts.get("videoStreams"):
        try:
            result = video.extract_video(path, ctx, run=run)
        except RuntimeError:
            return None
        result.warnings.insert(0, f"{ctx.file_name}: extension {ctx.extension or '(none)'} is not a video "
                                  f"extension; the bytes are a {facts.get('container', 'media')} container")
        return result
    if facts.get("audioStreams"):
        result = audio.extract_audio(path, ctx)
        sys_media = dict(result.attributes.get("sys_media") or {})
        # tinytag knows the common audio containers; whatever it did not read, the ffmpeg header supplies.
        for key in ("durationSeconds", "bitrateKbps", "audioCodec", "sampleRate", "channels", "channelLayout",
                    "container", "tags", "streamTags"):
            if key in facts and key not in sys_media:
                sys_media[key] = facts[key]
        sys_media.setdefault("kind", "audio")
        result.attributes["sys_media"] = sys_media
        result.file_class = CLASS_AUDIO
        result.warnings.insert(0, f"{ctx.file_name}: extension {ctx.extension or '(none)'} is not an audio "
                                  f"extension; the bytes are a {facts.get('container', 'media')} container")
        return result
    return None


def _archive_entries(names: List[str], sizes: List[int]) -> Dict[str, object]:
    extensions: Dict[str, int] = {}
    for name in names:
        ext = os.path.splitext(name)[1].lower()
        if ext:
            extensions[ext] = extensions.get(ext, 0) + 1
    top_level = sorted({name.split("/", 1)[0] for name in names if name})
    section: Dict[str, object] = {
        "entryCount": len(names),
        "uncompressedBytes": int(sum(sizes)),
        "entries": names[:ARCHIVE_MAX_LISTED_ENTRIES],
        "topLevelEntries": top_level[:ARCHIVE_MAX_LISTED_ENTRIES],
    }
    if extensions:
        section["entryExtensions"] = dict(sorted(extensions.items(), key=lambda item: (-item[1], item[0]))[:50])
    return section


def _probe_zip(path: str, ctx: ExtractContext) -> Optional[BranchResult]:
    if not zipfile.is_zipfile(path):
        return None
    result = BranchResult(file_class=CLASS_OTHER)
    try:
        with zipfile.ZipFile(path) as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            names = [info.filename for info in infos]
            section = _archive_entries(names, [info.file_size for info in infos])
            section["containerFormat"] = "zip"
            if _OPC_CORE_PART in archive.namelist():
                core = bounded_tags(_xml_leaf_values(archive.read(_OPC_CORE_PART)))
                if core:
                    section["coreProperties"] = core
            if _OPC_APP_PART in archive.namelist():
                app = bounded_tags(_xml_leaf_values(archive.read(_OPC_APP_PART)))
                if app:
                    section["appProperties"] = app
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        result.warnings.append(f"{ctx.file_name}: zip container could not be read: {exc}")
        return result
    result.attributes["sys_archive"] = section
    result.facts["container"] = "zip archive"
    result.facts["entries"] = f"{section['entryCount']:,} {'entry' if section['entryCount'] == 1 else 'entries'}"
    return result


def _probe_tar(path: str, ctx: ExtractContext) -> Optional[BranchResult]:
    try:
        if not tarfile.is_tarfile(path):
            return None
    except (OSError, ValueError):
        return None
    result = BranchResult(file_class=CLASS_OTHER)
    try:
        with tarfile.open(path) as archive:
            members = [member for member in archive.getmembers() if member.isfile()]
            section = _archive_entries([member.name for member in members], [member.size for member in members])
            section["containerFormat"] = "tar"
    except (tarfile.TarError, OSError) as exc:
        result.warnings.append(f"{ctx.file_name}: tar container could not be read: {exc}")
        return result
    result.attributes["sys_archive"] = section
    result.facts["container"] = "tar archive"
    result.facts["entries"] = f"{section['entryCount']:,} {'entry' if section['entryCount'] == 1 else 'entries'}"
    return result


def _decodes_as_text(sample: bytes) -> bool:
    if not sample:
        return False
    if sample.startswith((b"\xff\xfe", b"\xfe\xff")):
        return True
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError as error:
        if error.start < len(sample) - 3:
            return False
    return not any(byte in _TEXT_CONTROL_BYTES for byte in sample)


def _probe_text(path: str, ctx: ExtractContext) -> Optional[BranchResult]:
    try:
        with open(path, "rb") as handle:
            sample = handle.read(_TEXT_SAMPLE_BYTES)
    except OSError:
        return None
    if not _decodes_as_text(sample):
        return None
    result = text.extract_text(path, ctx)
    result.warnings.insert(0, f"{ctx.file_name}: extension {ctx.extension or '(none)'} is not a text "
                              f"extension; the bytes decode as text")
    return result


def extract_generic(path: str, ctx: ExtractContext, run: Callable = subprocess.run) -> BranchResult:
    """The probe chain for a file of no known class; the first probe that recognises the bytes decides.
    A probe that fails outright is noted on the fallback result rather than failing the file."""
    probes = (
        ("image", _probe_image),
        ("media", lambda p, c: _probe_media(p, c, run=run)),
        ("zip", _probe_zip),
        ("tar", _probe_tar),
        ("text", _probe_text),
    )
    failures: List[str] = []
    for name, probe in probes:
        try:
            result = probe(path, ctx)
        except Exception as exc:  # noqa: BLE001 - one probe's failure is not the file's
            failures.append(f"{name} probe failed: {exc}")
            continue
        if result is not None:
            return result
    fallback = other_fallback(ctx, "no probe recognised the format")
    fallback.warnings.extend(f"{ctx.file_name}: {failure}" for failure in failures)
    return fallback
