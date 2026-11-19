# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The `other` class: a file whose extension names no class. The bytes are probed for a format this image
reads and the matching extractor runs, so a `.bak` that is a JPEG is analysed as an image and a `.dat` that is
an MP4 as a video — the result's file class says what was found. A zip or tar container is described under
`sys_archive` (entry count, uncompressed size, the leading entry names and, for an OPC package, its core
properties); readable text takes the text extractor. A file no probe recognises keeps `other` with the
constructPipeline `sys_file` facts (size, content type, last-modified time, S3 metadata, detected format).

The probes run in order and the first that recognises the file decides: Pillow, then an ffmpeg header read
(a video stream -> video, audio only -> audio), then the archive formats, then a text decode.

A zip container is listed from its end-of-central-directory record and central directory only (`list_zip`, over
any seekable file object), so the handler lists one of any size by ranged reads of the S3 object without
downloading it; a central directory over ZIP_CENTRAL_DIRECTORY_MAX_BYTES is not parsed, and the record's entry
count and directory size stand with `listing: "skipped"`. No member is decompressed except the two OPC docProps
parts, each bounded. A tar container's header scan stops at TAR_SCAN_MAX_MEMBERS members or TAR_SCAN_MAX_SECONDS
seconds, whichever comes first, and its listing is then marked `partial`."""

import os
import struct
import subprocess
import tarfile
import time
import zipfile
from typing import Callable, Dict, List, Optional

import defusedxml
from defusedxml import ElementTree
from PIL import Image, UnidentifiedImageError

from . import audio, images, text, video
from .common import (
    ARCHIVE_MAX_LISTED_ENTRIES,
    CLASS_AUDIO,
    CLASS_OTHER,
    TAR_SCAN_MAX_MEMBERS,
    TAR_SCAN_MAX_SECONDS,
    ZIP_CENTRAL_DIRECTORY_MAX_BYTES,
    ZIP_TAIL_READ_BYTES,
    BranchResult,
    ExtractContext,
    ZipMemberTooLarge,
    bounded_tags,
    other_fallback,
    read_zip_member,
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
    """The (tag, text) pairs of the root's leaf children. Parsed through defusedxml, which raises
    DefusedXmlException for a document that declares entities or references external ones; a malformed
    document yields nothing."""
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
        result = audio.extract_audio(path, ctx, run=run)
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


def _opc_part_properties(archive: zipfile.ZipFile, part: str, ctx: ExtractContext,
                         result: BranchResult) -> Dict[str, object]:
    """The bounded leaf values of one OPC properties part. A part over the read limit, one that declares
    entities, or one the reader could not fetch (a ranged read over its bound) is not parsed and is noted on the
    result instead."""
    try:
        return bounded_tags(_xml_leaf_values(read_zip_member(archive, part)))
    except ZipMemberTooLarge as exc:
        result.warnings.append(f"{ctx.file_name}: {part} was not read: {exc}")
    except defusedxml.DefusedXmlException as exc:
        result.warnings.append(f"{ctx.file_name}: {part} was not read: it declares entities or external "
                               f"references ({type(exc).__name__})")
    except (OSError, zipfile.BadZipFile) as exc:
        result.warnings.append(f"{ctx.file_name}: {part} was not read: {exc}")
    return {}


# The end-of-central-directory record (22 bytes), its ZIP64 locator (20) and the ZIP64 record (56).
_EOCD_SIGNATURE = b"PK\x05\x06"
_EOCD_STRUCT = struct.Struct("<4s4H2LH")
_ZIP64_LOCATOR_SIGNATURE = b"PK\x06\x07"
_ZIP64_LOCATOR_STRUCT = struct.Struct("<4sLQL")
_ZIP64_RECORD_SIGNATURE = b"PK\x06\x06"
_ZIP64_RECORD_STRUCT = struct.Struct("<4sQ2H2L4Q")


def read_zip_end_record(fileobj, size: int) -> Optional[Dict[str, int]]:
    """``{"entryCount", "centralDirectoryBytes", "centralDirectoryOffset"}`` from a zip container's
    end-of-central-directory record (its ZIP64 record when the classic one is saturated), found in the file's
    last ZIP_TAIL_READ_BYTES; ``None`` when the file has no such record (not a zip, or a truncated one) or the
    record names a directory larger than the file. ``entryCount`` counts every central-directory record,
    directories included."""
    if size < _EOCD_STRUCT.size:
        return None
    tail_start = max(0, size - ZIP_TAIL_READ_BYTES)
    fileobj.seek(tail_start)
    tail = fileobj.read(size - tail_start)

    def read_at(offset: int, length: int) -> bytes:
        if offset < 0 or offset + length > size:
            return b""
        if offset >= tail_start:
            return tail[offset - tail_start:offset - tail_start + length]
        fileobj.seek(offset)
        return fileobj.read(length)

    position = tail.rfind(_EOCD_SIGNATURE)
    while position >= 0 and position + _EOCD_STRUCT.size > len(tail):
        position = tail.rfind(_EOCD_SIGNATURE, 0, position)
    if position < 0:
        return None
    _sig, _disk, _cd_disk, _disk_entries, entries, cd_bytes, cd_offset, _comment = _EOCD_STRUCT.unpack_from(
        tail, position)
    record_offset = tail_start + position
    if entries == 0xFFFF or cd_bytes == 0xFFFFFFFF or cd_offset == 0xFFFFFFFF:
        locator = read_at(record_offset - _ZIP64_LOCATOR_STRUCT.size, _ZIP64_LOCATOR_STRUCT.size)
        if locator[:4] != _ZIP64_LOCATOR_SIGNATURE:
            return None
        zip64_offset = _ZIP64_LOCATOR_STRUCT.unpack(locator)[2]
        zip64 = read_at(zip64_offset, _ZIP64_RECORD_STRUCT.size)
        if zip64[:4] != _ZIP64_RECORD_SIGNATURE:
            # Data before the archive (a self-extracting stub) shifts every offset; the record sits just before
            # its locator.
            zip64 = read_at(record_offset - _ZIP64_LOCATOR_STRUCT.size - _ZIP64_RECORD_STRUCT.size,
                            _ZIP64_RECORD_STRUCT.size)
        if zip64[:4] != _ZIP64_RECORD_SIGNATURE:
            return None
        fields = _ZIP64_RECORD_STRUCT.unpack(zip64)
        entries, cd_bytes, cd_offset = fields[7], fields[8], fields[9]
    if cd_bytes > size:
        return None
    return {"entryCount": int(entries), "centralDirectoryBytes": int(cd_bytes),
            "centralDirectoryOffset": int(cd_offset)}


def _zip_facts(result: BranchResult, section: Dict[str, object]) -> None:
    result.attributes["sys_archive"] = section
    result.facts["container"] = "zip archive"
    count = int(section["entryCount"])
    entries = f"{count:,} {'entry' if count == 1 else 'entries'}"
    if section.get("listing") == "skipped":
        entries += " (directory not read)"
    result.facts["entries"] = entries


def list_zip(fileobj, size: int, ctx: ExtractContext) -> Optional[BranchResult]:
    """The `sys_archive` description of the zip container ``fileobj`` (``size`` bytes, any seekable file object:
    a local file or the handler's ranged S3 reader). Only the end-of-central-directory record and the central
    directory are read, and the two OPC docProps parts when the archive carries them. A central directory over
    ZIP_CENTRAL_DIRECTORY_MAX_BYTES is not parsed: the record's entry count and the directory's size are recorded
    with ``listing: "skipped"`` and the reason. ``None`` when the file carries no end-of-central-directory record,
    so the caller falls back to the probe chain; a directory that does not parse keeps the class `other` with a
    warning and no `sys_archive`."""
    end = read_zip_end_record(fileobj, size)
    if end is None:
        return None
    result = BranchResult(file_class=CLASS_OTHER)
    if end["centralDirectoryBytes"] > ZIP_CENTRAL_DIRECTORY_MAX_BYTES:
        note = (f"the central directory is {end['centralDirectoryBytes']:,} bytes, over the "
                f"{ZIP_CENTRAL_DIRECTORY_MAX_BYTES:,}-byte bound, so it was not read; entryCount is the record count "
                f"the end-of-central-directory record states, directories included")
        section: Dict[str, object] = {
            "containerFormat": "zip",
            "entryCount": end["entryCount"],
            "centralDirectoryBytes": end["centralDirectoryBytes"],
            "listing": "skipped",
            "listingNote": note,
        }
        result.warnings.append(f"{ctx.file_name}: zip entries not listed: {note}")
        _zip_facts(result, section)
        return result
    try:
        with zipfile.ZipFile(fileobj) as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            names = [info.filename for info in infos]
            section = _archive_entries(names, [info.file_size for info in infos])
            section["containerFormat"] = "zip"
            if _OPC_CORE_PART in archive.NameToInfo:
                core = _opc_part_properties(archive, _OPC_CORE_PART, ctx, result)
                if core:
                    section["coreProperties"] = core
            if _OPC_APP_PART in archive.NameToInfo:
                app = _opc_part_properties(archive, _OPC_APP_PART, ctx, result)
                if app:
                    section["appProperties"] = app
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError) as exc:
        result.warnings.append(f"{ctx.file_name}: zip container could not be read: {exc}")
        return result
    _zip_facts(result, section)
    return result


def _probe_zip(path: str, ctx: ExtractContext) -> Optional[BranchResult]:
    if not zipfile.is_zipfile(path):
        return None
    with open(path, "rb") as handle:
        return list_zip(handle, os.path.getsize(path), ctx)


def _probe_tar(path: str, ctx: ExtractContext, clock: Callable[[], float] = time.monotonic) -> Optional[BranchResult]:
    try:
        if not tarfile.is_tarfile(path):
            return None
    except (OSError, ValueError):
        return None
    result = BranchResult(file_class=CLASS_OTHER)
    names: List[str] = []
    sizes: List[int] = []
    scanned = 0
    stopped = None
    try:
        with tarfile.open(path) as archive:
            started = clock()
            # Member by member: a compressed tar is decompressed as the scan moves through it, so the scan stops at
            # whichever bound it reaches first rather than reading the whole stream.
            for member in archive:
                if scanned >= TAR_SCAN_MAX_MEMBERS:
                    stopped = f"the header scan stopped at the {TAR_SCAN_MAX_MEMBERS:,}-member bound"
                    break
                if clock() - started > TAR_SCAN_MAX_SECONDS:
                    stopped = f"the header scan stopped at the {TAR_SCAN_MAX_SECONDS}-second bound"
                    break
                scanned += 1
                if member.isfile():
                    names.append(member.name)
                    sizes.append(member.size)
    except (tarfile.TarError, OSError, EOFError) as exc:
        if not scanned:
            result.warnings.append(f"{ctx.file_name}: tar container could not be read: {exc}")
            return result
        stopped = f"the header scan stopped at an unreadable member ({type(exc).__name__})"
    section = _archive_entries(names, sizes)
    section["containerFormat"] = "tar"
    if stopped:
        section["partial"] = True
        section["listingNote"] = (f"{stopped}; entryCount and uncompressedBytes count the files among the "
                                  f"{scanned:,} members read")
        result.warnings.append(f"{ctx.file_name}: tar listing is partial: {section['listingNote']}")
    result.attributes["sys_archive"] = section
    result.facts["container"] = "tar archive"
    result.facts["entries"] = (f"{section['entryCount']:,} {'entry' if section['entryCount'] == 1 else 'entries'}"
                               + (" (partial)" if stopped else ""))
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
