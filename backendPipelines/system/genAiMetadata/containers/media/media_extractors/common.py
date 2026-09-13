# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared vocabulary of the MEDIA branch: the file classes it serves, the extension table behind them, the
size and text budgets, and the result shape every extractor hands back to the handler."""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

RENDER_BRANCH = "MEDIA"

CLASS_IMAGE = "image"
CLASS_VIDEO = "video"
CLASS_AUDIO = "audio"
CLASS_DOCUMENT = "document"
CLASS_TEXT = "text"
CLASS_DATA = "data"
CLASS_TILES3D = "tiles3d"
CLASS_OTHER = "other"

# The classes constructPipeline may route here. `tiles3d` is also reached by promotion from `text`, and
# `other` only by demotion when a `text`/`data` file turns out not to be text at all.
MEDIA_CLASSES = (CLASS_IMAGE, CLASS_VIDEO, CLASS_AUDIO, CLASS_DOCUMENT, CLASS_TEXT, CLASS_DATA, CLASS_TILES3D)

# Extension -> fileClass, lower-case with the leading dot. `.svg` is `image` but takes the vector path;
# `.json` enters as `text` and is reclassified from its parsed root: `asset` + `geometricError` -> `tiles3d`,
# a GeoJSON root -> `data`. The office formats (.docx .pptx .xlsx) are admitted by the pipeline's
# ADDITIONAL_EXTENSIONS although no viewer renders them. Outside CLASSIFIER_OVERRIDE_EXTENSIONS this table
# equals the MEDIA rows of the pipeline's `lambda/fileClassifier.py` EXTENSION_CLASSES; that suite diffs the two.
MEDIA_EXTENSION_CLASSES: Dict[str, str] = {
    ".png": CLASS_IMAGE, ".jpg": CLASS_IMAGE, ".jpeg": CLASS_IMAGE, ".gif": CLASS_IMAGE, ".webp": CLASS_IMAGE,
    ".svg": CLASS_IMAGE,
    ".mp4": CLASS_VIDEO, ".webm": CLASS_VIDEO, ".mov": CLASS_VIDEO, ".avi": CLASS_VIDEO, ".mkv": CLASS_VIDEO,
    ".flv": CLASS_VIDEO, ".wmv": CLASS_VIDEO, ".m4v": CLASS_VIDEO,
    ".mp3": CLASS_AUDIO, ".wav": CLASS_AUDIO, ".ogg": CLASS_AUDIO, ".aac": CLASS_AUDIO, ".flac": CLASS_AUDIO,
    ".m4a": CLASS_AUDIO,
    ".pdf": CLASS_DOCUMENT, ".docx": CLASS_DOCUMENT, ".pptx": CLASS_DOCUMENT,
    ".txt": CLASS_TEXT, ".md": CLASS_TEXT, ".json": CLASS_TEXT, ".xml": CLASS_TEXT, ".yaml": CLASS_TEXT,
    ".yml": CLASS_TEXT, ".toml": CLASS_TEXT, ".ini": CLASS_TEXT, ".cfg": CLASS_TEXT, ".inf": CLASS_TEXT,
    ".log": CLASS_TEXT, ".py": CLASS_TEXT, ".js": CLASS_TEXT, ".ts": CLASS_TEXT, ".sql": CLASS_TEXT,
    ".sh": CLASS_TEXT, ".ps1": CLASS_TEXT, ".ipynb": CLASS_TEXT, ".html": CLASS_TEXT, ".htm": CLASS_TEXT,
    ".csv": CLASS_DATA, ".fcs": CLASS_DATA, ".xlsx": CLASS_DATA,
}

# Entries the pipeline's classifier treats differently from this table, each with its reason: `.webp` is a
# spec §6.3 image no viewer serves, so the allow list keeps it out of the pipeline (this image still handles
# it on a direct invocation); `.json` is sniffed by the classifier into `text` / `tiles3d` / `data` (GeoJSON) /
# `other` rather than mapped. Every other extension here carries the classifier's class.
CLASSIFIER_OVERRIDE_EXTENSIONS: Dict[str, str] = {
    ".webp": "not allow-listed: no viewer declares it",
    ".json": "sniffed by the classifier: text / tiles3d / data (GeoJSON) / other",
}

# The attribute keys the pipeline's `lambda/metadataCatalog.py` promotes to typed `ext_*` metadata and to the
# `location` GeoJSON, per group, nested objects as dotted paths. Every extractor writes these names verbatim;
# a section may carry more keys, never a contract key under another name.
PROMOTION_SOURCE_KEYS: Dict[str, Tuple[str, ...]] = {
    "sys_image": ("width", "height", "mode", "exif"),
    "sys_image.exif": ("make", "model", "dateTimeOriginal", "gps"),
    "sys_image.exif.gps": ("latitude", "longitude", "altitude"),
    "sys_media": ("kind", "durationSeconds", "width", "height", "frameRate", "videoCodec", "audioCodec",
                  "bitrateKbps", "channels", "sampleRate", "tags"),
    "sys_media.tags": ("title", "artist", "album", "year"),
    "sys_document": ("pageCount", "title", "author", "createdAt", "hasText"),
    "sys_text": ("encoding", "lineCount", "wordCount", "language"),
    "sys_data": ("columnCount", "rowCount", "columns"),
    "sys_tiles3d": ("geometricError", "tileCount", "region"),
    "sys_geo": ("featureCount", "geometryTypes", "footprint"),
}

# Vision-model input bounds: Anthropic models on Bedrock accept 3.75 MB per image and downsize anything past
# a 1568 px long edge, so the image is fitted here rather than billed and shrunk there.
VISION_MAX_LONG_EDGE_PX = 1568
VISION_MAX_BYTES = 3_750_000
# A raster above this pixel count is described from its header and not decoded.
MAX_RASTER_PIXELS = 80_000_000
# Text budgets. DEFAULT_MAX_TEXT_CHARS is the template tag MAX_TEXT_CHARS default.
DEFAULT_MAX_TEXT_CHARS = 12_000
TEXT_HEAD_BYTES = 4 * 1024 * 1024
SVG_MAX_BYTES = 20 * 1024 * 1024
# Video.
VIDEO_KEYFRAME_COUNT = 4
FFMPEG_TIMEOUT_SECONDS = 120
# Documents.
PDF_RASTER_PAGES = 2
# Audio.
AUDIO_MAX_TAGS = 50
AUDIO_TAG_VALUE_MAX_CHARS = 500
# Tabular data.
DATA_SAMPLE_ROWS = 5
DATA_MAX_COLUMNS = 200
DATA_CELL_MAX_CHARS = 200
DATA_MAX_ROWS_COUNTED = 2_000_000
DATA_SNIFF_BYTES = 64 * 1024

RENDER_SKIPPED_SIZE = "size"
RENDER_SKIPPED_UNSUPPORTED = "unsupported"
RENDER_SKIPPED_ERROR = "error"

_WHITESPACE_RUN = re.compile(r"\s+")
_YEAR_PREFIX = re.compile(r"^\s*(\d{4})")


@dataclass
class ExtractContext:
    """What an extractor knows about the file beyond its bytes. `extract_geo_location` is the state's
    `extractGeoLocation` (template tag EXTRACT_GEO_LOCATION): when false, no coordinates are written.
    `capture_full_text` asks a document, text or data extractor to keep the whole text and its page boundaries
    for content chunking; the excerpt stays bounded by `max_text_chars` either way."""

    file_name: str
    extension: str
    content_type: str
    max_text_chars: int
    work_dir: str
    extract_geo_location: bool = False
    capture_full_text: bool = False


@dataclass
class BranchResult:
    """One extractor's contribution to the analysis manifest. `render_images` are local PNG paths the handler
    uploads in order; `attributes` carries only this class's `sys_*` section (`sys_file` is already in the
    manifest constructPipeline wrote). `full_text` is the whole extracted text when the context asked for it
    (`""` otherwise), `page_offsets` its page or sheet boundaries as `{"page", "start"[, "name"]}` entries in
    `full_text` positions, and `full_text_truncated` whether the extractor's own read was bounded."""

    file_class: str
    attributes: Dict[str, dict] = field(default_factory=dict)
    render_images: List[str] = field(default_factory=list)
    text_excerpt: str = ""
    facts: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    render_skipped: Optional[str] = None
    full_text: str = ""
    full_text_truncated: bool = False
    page_offsets: List[dict] = field(default_factory=list)


def class_for_extension(extension: Optional[str]) -> Optional[str]:
    return MEDIA_EXTENSION_CLASSES.get((extension or "").lower())


def truncate_text(text: str, limit: int) -> str:
    """The first `limit` characters, cut back to the last whitespace inside the final tenth when there is one."""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    window_start = int(limit * 0.9)
    breaks = [match.start() for match in _WHITESPACE_RUN.finditer(cut, window_start)]
    if breaks:
        cut = cut[:breaks[-1]]
    return cut.rstrip()


def human_duration(seconds: float) -> str:
    """`7 s`, `1 min 32 s`, `2 h 05 min`."""
    total = int(round(max(float(seconds), 0.0)))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours} h {minutes:02d} min"
    if minutes:
        return f"{minutes} min {secs} s"
    return f"{secs} s"


def human_count(count: int, noun: str) -> str:
    """`1 page`, `1,234 pages`."""
    return f"{count:,} {noun}{'' if count == 1 else 's'}"


def year_number(value) -> Optional[int]:
    """The four-digit year a tag value starts with (`2026`, `2026-03-01`, `1999/12`), as an int; an int is
    kept when it is a plausible year; None otherwise."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 < value < 10000 else None
    match = _YEAR_PREFIX.match(str(value))
    return int(match.group(1)) if match else None


def other_fallback(ctx: ExtractContext, reason: str) -> BranchResult:
    """The `other` class: `sys_file` only, no analysis input beyond the file name and asset context."""
    return BranchResult(
        file_class=CLASS_OTHER,
        render_skipped=RENDER_SKIPPED_UNSUPPORTED,
        warnings=[f"{ctx.file_name}: {reason}; sys_file attributes only"],
    )
