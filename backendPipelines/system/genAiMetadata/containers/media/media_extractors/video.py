# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Video (.mp4 .webm .mov .avi .mkv .flv .wmv .m4v): stream facts -> sys_media and four evenly spaced keyframes
as PNG. The imageio-ffmpeg wheel bundles a static ffmpeg and no ffprobe, so the facts are parsed from the
header ffmpeg prints while decoding one frame per stream to the null muxer, cross-filled from imageio-ffmpeg's
own parsed metadata."""

import os
import re
import subprocess
from typing import Callable, Dict, List, Optional, Tuple

import imageio_ffmpeg
from PIL import Image

from .common import (
    CLASS_VIDEO,
    FFMPEG_TIMEOUT_SECONDS,
    RENDER_SKIPPED_ERROR,
    VIDEO_KEYFRAME_COUNT,
    VISION_MAX_LONG_EDGE_PX,
    BranchResult,
    ExtractContext,
    human_duration,
    year_number,
)
from .imaging import normalise_for_vision, write_png

_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_BITRATE = re.compile(r"bitrate:\s*(\d+)\s*kb/s")
_INPUT_FORMAT = re.compile(r"^Input #0,\s*([^,]+(?:,[^,\s]+)*),\s*from", re.MULTILINE)
_STREAM = re.compile(
    r"^\s*Stream #(\d+):(\d+)(?:\[[^\]]*\])?(?:\([^)]*\))?:\s*(Video|Audio|Subtitle|Data|Attachment):\s*(.*)$",
    re.MULTILINE)
_SIZE = re.compile(r"\b(\d{2,5})x(\d{2,5})\b")
_FPS = re.compile(r"\b(\d+(?:\.\d+)?)\s*fps\b")
_KBPS = re.compile(r"\b(\d+)\s*kb/s\b")
_HZ = re.compile(r"\b(\d+)\s*Hz\b")
_CHANNELS = re.compile(r"\d+\s*Hz,\s*([^,]+)")
_ROTATE_META = re.compile(r"rotate\s*:\s*(-?\d+)")
_ROTATE_MATRIX = re.compile(r"rotation of\s*(-?\d+(?:\.\d+)?)\s*degrees")
_TOKEN = re.compile(r"[A-Za-z0-9_]+")
# Splits a stream description on commas that are not inside parentheses (`yuv420p(tv, bt709)` is one field).
_STREAM_FIELD_SPLIT = re.compile(r",\s*(?![^()]*\))")
# Format-level metadata lines are indented exactly four spaces; stream-level ones eight.
_TAG_LINE = re.compile(r"^ {4}(title|artist|album|date|year)\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_TAG_VALUE_MAX_CHARS = 500
# ffmpeg channel-layout words -> channel counts; `5.1(side)` is looked up as `5.1`.
_CHANNEL_LAYOUTS = {"mono": 1, "stereo": 2, "2.1": 3, "3.0": 3, "quad": 4, "4.0": 4, "5.0": 5, "5.1": 6,
                    "6.1": 7, "7.1": 8}
_CHANNEL_COUNT = re.compile(r"^(\d+)\s*channels?$")


def _first_token(text: str) -> str:
    match = _TOKEN.search(text)
    return match.group(0) if match else text.strip()


def parse_channel_count(layout: Optional[str]) -> Optional[int]:
    """`stereo` -> 2, `5.1(side)` -> 6, `2 channels` -> 2; None for a layout the table does not name."""
    if not layout:
        return None
    name = str(layout).strip().lower().split("(", 1)[0].strip()
    if name in _CHANNEL_LAYOUTS:
        return _CHANNEL_LAYOUTS[name]
    match = _CHANNEL_COUNT.match(name)
    return int(match.group(1)) if match else None


def parse_format_tags(head: str) -> Dict[str, object]:
    """`{title, artist, album, year}` from the input's format-level Metadata block (the lines between
    `Input #0` and `Duration:`); the first occurrence of each wins, `date`/`year` become an int year."""
    block = head.split("Duration:", 1)[0]
    tags: Dict[str, object] = {}
    for match in _TAG_LINE.finditer(block):
        key, value = match.group(1).lower(), match.group(2)
        if key in ("date", "year"):
            year = year_number(value)
            if year is not None:
                tags.setdefault("year", year)
        else:
            tags.setdefault(key, value[:_TAG_VALUE_MAX_CHARS])
    return tags


def parse_ffmpeg_header_text(text: str) -> Dict[str, object]:
    """Container, duration, bitrate, stream counts, format tags and the first video/audio stream's facts from
    the input section of ffmpeg's stderr (everything before the first `Output #`), under the sys_media names
    the promotion catalogue reads: videoCodec, frameRate, audioCodec, sampleRate, channels, tags."""
    head = text.split("Output #", 1)[0]
    facts: Dict[str, object] = {"streamCount": 0, "videoStreams": 0, "audioStreams": 0}
    match = _DURATION.search(head)
    if match:
        facts["durationSeconds"] = round(
            int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3)), 3)
    match = _BITRATE.search(head.split("Stream #", 1)[0])
    if match:
        facts["bitrateKbps"] = int(match.group(1))
    match = _INPUT_FORMAT.search(head)
    if match:
        facts["container"] = match.group(1)
    for stream in _STREAM.finditer(head):
        kind, description = stream.group(3), stream.group(4)
        facts["streamCount"] += 1
        if kind == "Video":
            facts["videoStreams"] += 1
            if "videoCodec" in facts:
                continue
            fields = _STREAM_FIELD_SPLIT.split(description)
            facts["videoCodec"] = _first_token(fields[0])
            if len(fields) > 1:
                facts["pixelFormat"] = _first_token(fields[1])
            size = _SIZE.search(description)
            if size:
                facts["width"], facts["height"] = int(size.group(1)), int(size.group(2))
            fps = _FPS.search(description)
            if fps:
                facts["frameRate"] = float(fps.group(1))
            kbps = _KBPS.search(description)
            if kbps:
                facts["videoBitrateKbps"] = int(kbps.group(1))
        elif kind == "Audio":
            facts["audioStreams"] += 1
            if "audioCodec" in facts:
                continue
            facts["audioCodec"] = _first_token(description)
            hz = _HZ.search(description)
            if hz:
                facts["sampleRate"] = int(hz.group(1))
            channels = _CHANNELS.search(description)
            if channels:
                layout = channels.group(1).strip()
                facts["channelLayout"] = layout
                count = parse_channel_count(layout)
                if count is not None:
                    facts["channels"] = count
            kbps = _KBPS.search(description)
            if kbps:
                facts["audioBitrateKbps"] = int(kbps.group(1))
    rotation = _ROTATE_MATRIX.search(head) or _ROTATE_META.search(head)
    facts["rotation"] = int(round(float(rotation.group(1)))) % 360 if rotation else 0
    tags = parse_format_tags(head)
    if tags:
        facts["tags"] = tags
    return facts


def run_ffmpeg(
    args: List[str],
    run: Callable = subprocess.run,
    timeout: int = FFMPEG_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess:
    """ffmpeg with stdin closed and both pipes captured. `run` is injectable so tests supply a fake."""
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    return run(
        [exe, "-hide_banner", "-nostdin", *args],
        stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, check=False)


def probe_video(
    path: str,
    run: Callable = subprocess.run,
    read_frames: Callable = imageio_ffmpeg.read_frames,
) -> Tuple[Dict[str, object], List[str]]:
    """(facts, warnings) from two independent sources; one failing is a warning, both failing is an error."""
    facts: Dict[str, object] = {}
    warnings: List[str] = []
    try:
        proc = run_ffmpeg(["-i", path, "-frames:v", "1", "-frames:a", "1", "-f", "null", "-"], run=run)
        stderr = proc.stderr.decode("utf-8", "replace")
        parsed = parse_ffmpeg_header_text(stderr)
        if proc.returncode != 0 and "videoCodec" not in parsed:
            warnings.append(f"ffmpeg header probe exited {proc.returncode}: {stderr[-300:].strip()}")
        else:
            facts.update(parsed)
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        warnings.append(f"ffmpeg header probe failed: {exc}")
    try:
        generator = read_frames(path)
        try:
            meta = next(generator)
        finally:
            generator.close()
        facts.setdefault("videoCodec", meta.get("codec"))
        facts.setdefault("pixelFormat", meta.get("pix_fmt"))
        facts.setdefault("audioCodec", meta.get("audio_codec"))
        source_size = meta.get("source_size") or meta.get("size")
        if source_size and "width" not in facts:
            facts["width"], facts["height"] = int(source_size[0]), int(source_size[1])
        if meta.get("fps") and "frameRate" not in facts:
            facts["frameRate"] = float(meta["fps"])
        if meta.get("duration") and "durationSeconds" not in facts:
            facts["durationSeconds"] = round(float(meta["duration"]), 3)
        if meta.get("rotate") and not facts.get("rotation"):
            facts["rotation"] = int(meta["rotate"]) % 360
    except Exception as exc:  # noqa: BLE001 - imageio raises IOError/RuntimeError/StopIteration on undecodable input
        warnings.append(f"imageio-ffmpeg metadata failed: {exc}")
    if "width" not in facts and "durationSeconds" not in facts:
        raise RuntimeError("No video facts could be read: " + "; ".join(warnings))
    facts.setdefault("rotation", 0)
    return {key: value for key, value in facts.items() if value is not None}, warnings


def keyframe_times(duration: float, count: int = VIDEO_KEYFRAME_COUNT) -> List[float]:
    """Midpoints of `count` equal segments: never the frame at 0 (often black), never the end of stream."""
    if duration <= 0:
        return [0.0]
    return [round(duration * (index + 0.5) / count, 3) for index in range(count)]


def extract_keyframes(
    path: str,
    duration: float,
    work_dir: str,
    run: Callable = subprocess.run,
) -> Tuple[List[str], List[str]]:
    """PNG paths for the keyframes that decoded, and one warning per keyframe that did not."""
    paths: List[str] = []
    warnings: List[str] = []
    for index, seconds in enumerate(keyframe_times(duration), start=1):
        raw = os.path.join(work_dir, f"keyframe-{index:02d}-raw.png")
        try:
            proc = run_ffmpeg([
                "-ss", f"{seconds:.3f}", "-i", path, "-an", "-sn", "-frames:v", "1", "-update", "1",
                "-vf", f"scale='min({VISION_MAX_LONG_EDGE_PX},iw)':-2", "-y", raw,
            ], run=run)
            if proc.returncode != 0 or not os.path.exists(raw):
                tail = proc.stderr.decode("utf-8", "replace")[-300:].strip()
                warnings.append(f"Keyframe at {seconds:.1f}s failed (exit {proc.returncode}): {tail}")
                continue
            with Image.open(raw) as frame:
                png, _ = normalise_for_vision(frame.copy())
            paths.append(write_png(png, work_dir, f"media-{index:02d}.png"))
        except (OSError, subprocess.TimeoutExpired) as exc:
            warnings.append(f"Keyframe at {seconds:.1f}s failed: {exc}")
    return paths, warnings


def extract_video(
    path: str,
    ctx: ExtractContext,
    run: Callable = subprocess.run,
    read_frames: Callable = imageio_ffmpeg.read_frames,
) -> BranchResult:
    result = BranchResult(file_class=CLASS_VIDEO)
    facts, warnings = probe_video(path, run=run, read_frames=read_frames)
    result.warnings.extend(warnings)
    duration = float(facts.get("durationSeconds") or 0.0)
    sys_media: Dict[str, object] = {"kind": "video", **facts}
    if int(facts.get("rotation") or 0) in (90, 270) and "width" in facts:
        sys_media["displayWidth"], sys_media["displayHeight"] = facts["height"], facts["width"]
    result.attributes["sys_media"] = sys_media
    if duration:
        result.facts["duration"] = human_duration(duration)
    if "width" in facts:
        width = sys_media.get("displayWidth", facts["width"])
        height = sys_media.get("displayHeight", facts["height"])
        result.facts["resolution"] = f"{width} x {height} px"
    if facts.get("frameRate"):
        result.facts["frameRate"] = f"{facts['frameRate']:g} fps"
    if facts.get("videoCodec"):
        result.facts["videoCodec"] = str(facts["videoCodec"])
    if facts.get("audioCodec"):
        result.facts["audioCodec"] = str(facts["audioCodec"])
    frames, frame_warnings = extract_keyframes(path, duration, ctx.work_dir, run=run)
    result.warnings.extend(frame_warnings)
    result.render_images = frames
    if not frames:
        result.render_skipped = RENDER_SKIPPED_ERROR
    return result
