# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Audio (.mp3 .wav .ogg .aac .flac .m4a): duration, stream facts and tags via tinytag -> sys_media. The tag
text is the analysis input; audio has no image."""

from typing import Dict, List, Optional

from tinytag import TinyTag, TinyTagException, UnsupportedFormatError

from .common import (
    AUDIO_MAX_TAGS,
    AUDIO_TAG_VALUE_MAX_CHARS,
    CLASS_AUDIO,
    BranchResult,
    ExtractContext,
    human_duration,
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


def container_name(tag: Optional[TinyTag], fallback: str) -> str:
    """The container label from the parser's MIME type, else `fallback` (the upper-cased extension)."""
    mime = (getattr(tag, "mime_type", None) or "").split(";")[0].strip().lower()
    return _CONTAINERS.get(mime, fallback)


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
    entries. Artwork lives on `.images`, never here; a binary value in `.other` is dropped."""
    rendered: Dict[str, str] = {}
    other = getattr(tag, "other", None)
    if tag is None or not isinstance(other, dict):
        return rendered
    entries = [(name, [getattr(tag, name, None), *_as_list(other.get(name, []))]) for name in _TAG_FIELDS]
    entries += [(name, _as_list(values)) for name, values in other.items() if name not in _TAG_FIELDS]
    for name, values in entries:
        text = _joined(values)
        if text is None:
            continue
        rendered[str(name)] = text[:AUDIO_TAG_VALUE_MAX_CHARS]
        if len(rendered) >= AUDIO_MAX_TAGS:
            break
    return rendered


def extract_audio(path: str, ctx: ExtractContext) -> BranchResult:
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
    if tag is None:
        result.attributes["sys_media"] = {"kind": "audio", "container": extension_label, "decodable": False}
        return result
    sys_media: Dict[str, object] = {
        "kind": "audio", "container": container_name(tag, extension_label), "decodable": True, **facts}
    if tags:
        # The record keeps tinytag's strings; `year` becomes the int the catalogue promotes to ext_year when
        # the text starts with a four-digit year.
        recorded_tags: Dict[str, object] = dict(tags)
        year = year_number(tags.get("year"))
        if year is not None:
            recorded_tags["year"] = year
        sys_media["tags"] = recorded_tags
    result.attributes["sys_media"] = sys_media
    if sys_media.get("durationSeconds"):
        result.facts["duration"] = human_duration(float(sys_media["durationSeconds"]))
    if sys_media.get("sampleRate"):
        result.facts["sampleRate"] = f"{sys_media['sampleRate']} Hz"
    if sys_media.get("channels"):
        result.facts["channels"] = str(sys_media["channels"])
    for name in _FACT_FIELDS:
        if name in tags:
            result.facts[name] = tags[name]
    result.text_excerpt = truncate_text(
        "\n".join(f"{key}: {value}" for key, value in tags.items()), ctx.max_text_chars)
    return result
