# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared vocabulary of the MEDIA branch: the file classes it serves, the extension table behind them, the
size and text budgets, and the result shape every extractor hands back to the handler."""

import json
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

# The classes constructPipeline may route here. `tiles3d` is also reached by promotion from `text`;
# `other` arrives for every extension the classifier does not name (and by demotion when a `text`/`data`
# file turns out not to be text at all) and takes the generic extractor, which probes the bytes for a
# format this image reads and otherwise records the file's own facts.
MEDIA_CLASSES = (CLASS_IMAGE, CLASS_VIDEO, CLASS_AUDIO, CLASS_DOCUMENT, CLASS_TEXT, CLASS_DATA, CLASS_TILES3D,
                 CLASS_OTHER)

# Extension -> fileClass, lower-case with the leading dot. `.svg` is `image` but takes the vector path;
# `.json` enters as `text` and is reclassified from its parsed root: `asset` + `geometricError` -> `tiles3d`,
# a GeoJSON root -> `data`. The office formats (.docx .pptx .xlsx) and the GIS containers (.gpkg GeoPackage,
# .lyrx ArcGIS Pro layer file; both `data`, read by `gis.py` without GDAL) are admitted by the pipeline's
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
    ".csv": CLASS_DATA, ".fcs": CLASS_DATA, ".xlsx": CLASS_DATA, ".gpkg": CLASS_DATA, ".lyrx": CLASS_DATA,
}

# Entries the pipeline's classifier treats differently from this table, each with its reason: `.webp` is a
# supported image extension no viewer serves, so the allow list keeps it out of the pipeline (this image still handles
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
                  "bitrateKbps", "channels", "sampleRate", "tags", "gps"),
    "sys_media.tags": ("title", "artist", "album", "year", "creation_time", "date"),
    "sys_media.gps": ("latitude", "longitude", "altitude"),
    "sys_archive": ("entryCount", "uncompressedBytes", "containerFormat"),
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
# Embedded metadata recorded verbatim from a container (EXIF/XMP/PNG text, ffmpeg format and stream tags,
# archive entries): per-block caps so a file cannot grow its attribute record without bound.
EMBEDDED_TAGS_MAX_KEYS = 200
EMBEDDED_TAG_VALUE_MAX_CHARS = 500
ARCHIVE_MAX_LISTED_ENTRIES = 50
# Tabular data.
DATA_SAMPLE_ROWS = 5
DATA_MAX_COLUMNS = 200
DATA_CELL_MAX_CHARS = 200
DATA_MAX_ROWS_COUNTED = 2_000_000
DATA_SNIFF_BYTES = 64 * 1024
# The largest input the MEDIA image downloads to its ephemeral storage (4096 MiB, shared with the renders, the
# captured text and the SQLite page cache). A larger file keeps the pre-written `sys_file` attributes and is not
# fetched; the handler records why.
INPUT_MAX_DOWNLOAD_BYTES = 3_500 * 1024 * 1024


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
    # The state's `renderImages`: false when the GenAI layer is off, so no extractor produces the PNGs
    # (normalised image, keyframes, page rasters) that exist only as analysis-model input.
    render_images: bool = True


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


# A container's position travels as an ISO 6709 string: ffmpeg maps the QuickTime `©xyz` atom and the 3GPP
# `loci` box to the `location` tag (with a language suffix for 3GPP, `location-eng`), and Apple devices also
# write `com.apple.quicktime.location.ISO6709`. The string is `±latitude±longitude[±altitude][/]`, each angle
# in degrees (`+47.6097-122.3331+056.000/`), degrees-minutes (`+4736.58-12219.98/`) or
# degrees-minutes-seconds (`+473635-1221959/`), told apart by the integer digit count.
_ISO6709 = re.compile(
    r"^\s*([+-]\d{2,6}(?:\.\d+)?)([+-]\d{3,7}(?:\.\d+)?)([+-]\d+(?:\.\d+)?)?(?:CRS[^/]*)?/?\s*$")
_GPS_DECIMALS = 6


def is_location_tag(key: str) -> bool:
    """Whether a container tag key carries a position (an ISO 6709 string)."""
    lowered = str(key).lower()
    return lowered == "location" or lowered.startswith("location-") or lowered.endswith(".location.iso6709")


def _iso6709_angle(token: str, degree_digits: int) -> Optional[float]:
    sign = -1.0 if token[0] == "-" else 1.0
    whole, _, fraction = token[1:].partition(".")
    extra = len(whole) - degree_digits
    if extra not in (0, 2, 4):
        return None
    fraction_value = float("0." + fraction) if fraction else 0.0
    degrees = float(whole[:degree_digits])
    minutes = float(whole[degree_digits:degree_digits + 2]) if extra >= 2 else 0.0
    seconds = float(whole[degree_digits + 2:degree_digits + 4]) if extra == 4 else 0.0
    if extra == 0:
        degrees += fraction_value
    elif extra == 2:
        minutes += fraction_value
    else:
        seconds += fraction_value
    if minutes >= 60 or seconds >= 60:
        return None
    return sign * (degrees + minutes / 60 + seconds / 3600)


def parse_iso6709(value) -> Optional[Dict[str, Optional[float]]]:
    """`{latitude, longitude, altitude}` in decimal degrees from an ISO 6709 string, or None when the text is
    not one or its angles are out of range. Altitude is None when the string carries none."""
    if value is None or isinstance(value, bool):
        return None
    match = _ISO6709.match(str(value))
    if not match:
        return None
    latitude = _iso6709_angle(match.group(1), 2)
    longitude = _iso6709_angle(match.group(2), 3)
    if latitude is None or longitude is None or abs(latitude) > 90 or abs(longitude) > 180:
        return None
    altitude = float(match.group(3)) if match.group(3) else None
    return {"latitude": round(latitude, _GPS_DECIMALS), "longitude": round(longitude, _GPS_DECIMALS),
            "altitude": altitude}


def record_media_position(sys_media: Dict[str, object], extract_geo_location: bool) -> Optional[str]:
    """Apply the `extractGeoLocation` gate to a `sys_media` record whose `tags` (and `streamTags`) are already
    filled, the way `sys_image.exif` is gated: when a position-bearing tag is present, `hasGps` is recorded;
    with the flag on, the first tag that decodes becomes `gps` (`latitude`, `longitude`, `altitude`); with the
    flag off, the position-bearing tags are withheld from the record so no coordinate is written. Returns a
    warning when the flag is on and a position tag is present but none decodes."""
    tags = sys_media.get("tags")
    tags = tags if isinstance(tags, dict) else {}
    stream_tags = sys_media.get("streamTags")
    stream_tags = stream_tags if isinstance(stream_tags, dict) else {}
    located = [key for key in tags if is_location_tag(key)]
    located_streams = {stream: [key for key in block if is_location_tag(key)]
                       for stream, block in stream_tags.items() if isinstance(block, dict)}
    if not located and not any(located_streams.values()):
        return None
    sys_media["hasGps"] = True
    if extract_geo_location:
        for key in located:
            gps = parse_iso6709(tags[key])
            if gps is not None:
                sys_media["gps"] = gps
                return None
        for stream, keys in located_streams.items():
            for key in keys:
                gps = parse_iso6709(stream_tags[stream][key])
                if gps is not None:
                    sys_media["gps"] = gps
                    return None
        return "Container location tag present but it did not decode as ISO 6709; no gps recorded"
    for key in located:
        del tags[key]
    if not tags:
        sys_media.pop("tags", None)
    for stream, keys in located_streams.items():
        for key in keys:
            del stream_tags[stream][key]
        if not stream_tags[stream]:
            del stream_tags[stream]
    if not stream_tags:
        sys_media.pop("streamTags", None)
    return None


def other_fallback(ctx: ExtractContext, reason: str) -> BranchResult:
    """The `other` class: `sys_file` only, no analysis input beyond the file name and asset context."""
    return BranchResult(
        file_class=CLASS_OTHER,
        render_skipped=RENDER_SKIPPED_UNSUPPORTED,
        warnings=[f"{ctx.file_name}: {reason}; sys_file attributes only"],
    )


def bounded_tags(items, max_keys: int = EMBEDDED_TAGS_MAX_KEYS,
                 max_chars: int = EMBEDDED_TAG_VALUE_MAX_CHARS) -> Dict[str, object]:
    """Embedded tags as a bounded dict: at most `max_keys` entries in the order given, each scalar value
    rendered as text of at most `max_chars`, bytes decoded leniently, numbers and booleans kept, blank
    values dropped. Lists and dicts are kept when small (their JSON text within the value cap)."""
    recorded: Dict[str, object] = {}
    for key, value in items:
        if len(recorded) >= max_keys:
            break
        name = str(key).strip()
        if not name or name in recorded:
            continue
        if value is None or isinstance(value, bool) or isinstance(value, (int, float)):
            if value is None:
                continue
            recorded[name] = value
            continue
        if isinstance(value, bytes):
            value = value.decode("utf-8", "replace").strip("\x00")
        if isinstance(value, (list, tuple, dict)):
            text = json.dumps(value, default=str, sort_keys=True)
            if len(text) > max_chars:
                text = text[:max_chars]
            recorded[name] = text
            continue
        text = str(value).strip()
        if not text:
            continue
        recorded[name] = text[:max_chars]
    return recorded
