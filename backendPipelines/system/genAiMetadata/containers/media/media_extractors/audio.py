# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Audio (.mp3 .wav .ogg .aac .flac .m4a): duration, stream facts and tags via tinytag -> sys_media, with the
ffmpeg header supplying what tinytag cannot name. The tag text is the analysis input; audio has no image.

tinytag reads the MP4 `ilst` atoms it knows by their four-character code; an atom written under the
`keys`/`mdta` scheme (how ffmpeg writes `location`, `creation_time` and other arbitrary tags into an .m4a) is
named by its 1-based index in the `keys` atom instead, so it surfaces under a key such as `\\x00\\x00\\x00\\x01`.
Those entries are dropped, and the format-level tags ffmpeg prints (which resolve the names) are merged in,
so a recording's position and time reach `sys_media.tags` under the names the promotion catalogue reads."""

import subprocess
from typing import Callable, Dict, List, Optional

from tinytag import TinyTag, TinyTagException, UnsupportedFormatError

from . import video
from .common import (
    AUDIO_MAX_TAGS,
    AUDIO_TAG_VALUE_MAX_CHARS,
    CLASS_AUDIO,
    BranchResult,
    ExtractContext,
    human_duration,
    is_location_tag,
    record_media_position,
    truncate_text,
    year_number,
)

# TinyTag stream property -> sys_media key (the promotion catalogue's names). tinytag reports duration in
# seconds and bitrate in kbit/s.
_INFO_FIELDS = (
    ("duration", "durationSeconds"), ("bitrate", "bitrateKbps"), ("samplerate", "sampleRate"),
    ("channels", "channels"), ("bitdepth", "bitsPerSample"),
)
# Parser MIME type (parameters stripped) -> container label; the extension names the container when the parser
# recorded none.
_CONTAINERS = {
    "audio/wav": "WAVE", "audio/mpeg": "MPEG", "audio/flac": "FLAC", "audio/ogg": "OGG", "audio/mp4": "MP4",
    "application/vnd.ms-asf": "ASF", "audio/aiff": "AIFF",
}
# The named TinyTag tag fields, in excerpt order; whatever else a file carries is under `.other`.
_TAG_FIELDS = (
    "title", "artist", "album", "albumartist", "composer", "genre", "year", "track", "track_total", "disc",
    "disc_total", "comment",
)
# Tag fields that double as facts.
_FACT_FIELDS = ("title", "artist", "album")
# Audio in an ISO base-media container, where tinytag names a `keys`/`mdta` atom by its index: the ffmpeg
# header is read for the tag names. Matched on the parser's MIME type, else on the extension.
_MP4_FAMILY_EXTENSIONS = frozenset({".m4a", ".m4b", ".m4p", ".m4r", ".mp4", ".3gp", ".3g2"})
# The ffmpeg header facts that fill a sys_media key tinytag left empty (the same set the generic probe uses).
_HEADER_FILL_KEYS = ("durationSeconds", "bitrateKbps", "audioCodec", "sampleRate", "channels", "channelLayout",
                     "container")


def container_name(tag: Optional[TinyTag], fallback: str) -> str:
    """The container label from the parser's MIME type, else `fallback` (the upper-cased extension)."""
    mime = (getattr(tag, "mime_type", None) or "").split(";")[0].strip().lower()
    return _CONTAINERS.get(mime, fallback)


def is_mp4_family(tag: Optional[TinyTag], extension: str) -> bool:
    """Whether the audio sits in an ISO base-media (MP4/QuickTime) container, by the parser's MIME type when it
    read one, else by extension."""
    mime = (getattr(tag, "mime_type", None) or "").split(";")[0].strip().lower()
    if mime:
        return mime == "audio/mp4"
    return extension.lower() in _MP4_FAMILY_EXTENSIONS


def probe_header(path: str, run: Callable = subprocess.run) -> Dict[str, object]:
    """The facts ffmpeg's input header reports for an audio file (container, duration, codec, format-level
    `tags`, per-stream `streamTags`), under the sys_media names; `{}` when ffmpeg is unavailable or the file does
    not parse. One audio frame is decoded, enough for the header to print."""
    try:
        proc = video.run_ffmpeg(["-i", path, "-frames:a", "1", "-f", "null", "-"], run=run)
    except (OSError, subprocess.TimeoutExpired, RuntimeError):
        return {}
    facts = video.parse_ffmpeg_header_text(proc.stderr.decode("utf-8", "replace"))
    if not facts.get("audioStreams"):
        return {}
    return facts


def header_facts(tag: Optional[TinyTag]) -> Dict[str, object]:
    """The stream properties tinytag read, under their sys_media keys; absent and zero values are left out."""
    facts: Dict[str, object] = {}
    for attribute, key in _INFO_FIELDS:
        value = getattr(tag, attribute, None)
        if value in (None, 0, ""):
            continue
        facts[key] = round(float(value), 3) if isinstance(value, float) else value
    if "bitrateKbps" in facts:
        facts["bitrateKbps"] = int(round(float(facts["bitrateKbps"])))
    return facts


def _as_list(values) -> List[object]:
    return list(values) if isinstance(values, (list, tuple)) else [values]


def _joined(values: List[object]) -> Optional[str]:
    """`", "`-joined text of the values; None when any value is binary or none carries text."""
    present = [value for value in values if value is not None]
    if not present or any(isinstance(value, bytes) for value in present):
        return None
    text = ", ".join(str(value).strip() for value in present if str(value).strip())
    return text or None


def tag_text(tag: Optional[TinyTag]) -> Dict[str, str]:
    """Tag values as bounded strings, at most AUDIO_MAX_TAGS: the named fields first (a field's further values,
    which tinytag files under the same name in `.other`, are joined onto it), then the remaining `.other`
    entries. Artwork lives on `.images`, never here; a binary value in `.other` is dropped, as is an entry whose
    name is an atom index (control characters, how tinytag names a `keys`/`mdta` atom) rather than a tag name."""
    rendered: Dict[str, str] = {}
    other = getattr(tag, "other", None)
    if tag is None or not isinstance(other, dict):
        return rendered
    entries = [(name, [getattr(tag, name, None), *_as_list(other.get(name, []))]) for name in _TAG_FIELDS]
    entries += [(name, _as_list(values)) for name, values in other.items() if name not in _TAG_FIELDS]
    for name, values in entries:
        name = str(name)
        if not name or not name.isprintable():
            continue
        text = _joined(values)
        if text is None:
            continue
        rendered[name] = text[:AUDIO_TAG_VALUE_MAX_CHARS]
        if len(rendered) >= AUDIO_MAX_TAGS:
            break
    return rendered


def extract_audio(path: str, ctx: ExtractContext, run: Callable = subprocess.run) -> BranchResult:
    result = BranchResult(file_class=CLASS_AUDIO)
    extension_label = ctx.extension.lstrip(".").upper()
    tag: Optional[TinyTag] = None
    try:
        tag = TinyTag.get(path)
    except UnsupportedFormatError:
        result.warnings.append("Audio container was not recognised")
    except TinyTagException as exc:  # ParseError: a recognised container whose header does not parse
        result.warnings.append(f"Audio header could not be parsed: {exc}")
    facts = header_facts(tag)
    tags = tag_text(tag)
    if tag is not None and not facts and not tags:
        # tinytag hands back an empty tag object rather than raising when it finds no frame it understands.
        tag = None
        result.warnings.append("Audio header carried no stream facts or tags")
    # An ISO base-media container names its `keys`/`mdta` atoms only in the ffmpeg header.
    header = probe_header(path, run=run) if is_mp4_family(tag, ctx.extension) else {}
    if tag is None and not header:
        result.attributes["sys_media"] = {"kind": "audio", "container": extension_label, "decodable": False}
        return result
    sys_media: Dict[str, object] = {
        "kind": "audio", "container": container_name(tag, extension_label), "decodable": True, **facts}
    for key in _HEADER_FILL_KEYS:
        if key in header and key not in sys_media:
            sys_media[key] = header[key]
    # The record keeps tinytag's strings; `year` becomes the int the catalogue promotes to ext_year when the
    # text starts with a four-digit year. The header's tags fill in under the names tinytag did not resolve.
    recorded_tags: Dict[str, object] = dict(tags)
    year = year_number(tags.get("year"))
    if year is not None:
        recorded_tags["year"] = year
    header_tags = header.get("tags")
    if isinstance(header_tags, dict):
        for key, value in header_tags.items():
            if len(recorded_tags) >= AUDIO_MAX_TAGS:
                break
            recorded_tags.setdefault(key, value)
    if recorded_tags:
        sys_media["tags"] = recorded_tags
    if isinstance(header.get("streamTags"), dict):
        sys_media["streamTags"] = {stream: dict(block) for stream, block in header["streamTags"].items()
                                   if isinstance(block, dict)}
    result.attributes["sys_media"] = sys_media
    position_warning = record_media_position(sys_media, ctx.extract_geo_location)
    if position_warning:
        result.warnings.append(position_warning)
    if sys_media.get("durationSeconds"):
        result.facts["duration"] = human_duration(float(sys_media["durationSeconds"]))
    if sys_media.get("sampleRate"):
        result.facts["sampleRate"] = f"{sys_media['sampleRate']} Hz"
    if sys_media.get("channels"):
        result.facts["channels"] = str(sys_media["channels"])
    kept_tags = sys_media.get("tags") if isinstance(sys_media.get("tags"), dict) else {}
    for name in _FACT_FIELDS:
        if name in kept_tags:
            result.facts[name] = str(kept_tags[name])
    recorded = kept_tags.get("creation_time") or kept_tags.get("date")
    if recorded:
        result.facts["recorded"] = str(recorded)
    gps = sys_media.get("gps")
    if isinstance(gps, dict):
        result.facts["location"] = f"{gps['latitude']}, {gps['longitude']}"
    # The tag text is the model's input, so a withheld position tag stays out of it as well; the excerpt keeps
    # the text tinytag read (the record's `year` is the int the catalogue promotes).
    excerpt_source: Dict[str, object] = dict(tags)
    for key, value in recorded_tags.items():
        excerpt_source.setdefault(key, value)
    excerpt_tags = {key: value for key, value in excerpt_source.items()
                    if ctx.extract_geo_location or not is_location_tag(key)}
    result.text_excerpt = truncate_text(
        "\n".join(f"{key}: {value}" for key, value in excerpt_tags.items()), ctx.max_text_chars)
    return result
