# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""ffmpeg and ffprobe, reached through one subprocess seam so the stages are testable without either binary."""

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field

from PIL import Image

from .errors import INPUT_REJECTED, PipelineRejection

logger = logging.getLogger("video_sop_bom_pipeline.media")

TAIL_LINES = 400
PROBE_TIMEOUT_S = 300
EXTRACT_TIMEOUT_S = 3 * 3600
FRAME_TIMEOUT_S = 300

FRAME_MAX_EDGE = 1568
FRAME_JPEG_QUALITY = 85
FRAME_FALLBACK_QUALITIES = (75, 65, 50)
FRAME_MAX_BYTES = 3_932_160  # 3.75 MiB, the Converse per-image limit

# Mono 16 kHz FLAC: Transcribe's recommended lossless format, core encoder (no libmp3lame dependency).
AUDIO_ARGS = ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "flac"]
FFMPEG_BASE = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
PROBE_ARGS = ["-v", "error", "-show_entries", "stream=index,codec_type,codec_name,duration:format=duration", "-of", "json"]


class FrameExtractionFailed(Exception):
    """One key frame could not be produced; the caller records it as skipped and continues."""


def _tail(text):
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    lines = (text or "").splitlines()
    return "\n".join(lines[-TAIL_LINES:])


def _run(argv, timeout):
    """Run one media command; returns (returncode, bounded tail of stdout+stderr). Never raises for the
    command's own failure: a missing binary is 127, a timeout is 124."""
    try:
        completed = subprocess.run(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout if exc.stdout is not None else exc.output
        return 124, (_tail(partial or "") + f"\n{argv[0]} timed out after {timeout}s").strip()
    except FileNotFoundError:
        return 127, f"{argv[0]}: not found"
    return completed.returncode, _tail(completed.stdout)


def _last_line(tail, fallback):
    lines = [line for line in (tail or "").splitlines() if line.strip()]
    return lines[-1] if lines else fallback


def _as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class Probe:
    duration_s: float
    has_audio: bool
    streams: list = field(default_factory=list)


def probe(path):
    """Stream and duration inspection. A non-zero ffprobe exit is an input rejection, never a 0 duration."""
    name = os.path.basename(path)
    rc, tail = _run(["ffprobe", *PROBE_ARGS, path], PROBE_TIMEOUT_S)
    if rc != 0:
        raise PipelineRejection(INPUT_REJECTED, f"{name} could not be decoded (ffprobe exit {rc}: {_last_line(tail, 'no output')}).")
    try:
        data = json.loads(tail)
    except json.JSONDecodeError as exc:
        raise PipelineRejection(INPUT_REJECTED, f"{name} could not be probed: ffprobe returned no stream information.") from exc
    streams = data.get("streams") or []
    has_audio = any(stream.get("codec_type") == "audio" for stream in streams)
    duration = _as_float((data.get("format") or {}).get("duration"))
    if duration <= 0:
        duration = max((_as_float(stream.get("duration")) for stream in streams), default=0.0)
    logger.info("probe %s duration=%.3fs streams=%d audio=%s", name, duration, len(streams), has_audio)
    return Probe(duration_s=duration, has_audio=has_audio, streams=streams)


def extract_audio_flac(src, dst):
    """One video -> mono 16 kHz FLAC; returns the extracted track's duration (what Transcribe will time)."""
    rc, tail = _run([*FFMPEG_BASE, "-i", src, *AUDIO_ARGS, dst], EXTRACT_TIMEOUT_S)
    if rc != 0 or not os.path.isfile(dst):
        raise PipelineRejection(
            INPUT_REJECTED,
            f"{os.path.basename(src)}: audio extraction failed (ffmpeg exit {rc}: {_last_line(tail, 'no output')}).",
        )
    return probe(dst).duration_s


def concat_flac(parts, dst):
    """Concatenate the per-video tracks into one re-encoded FLAC; returns its duration."""
    list_path = dst + ".txt"
    with open(list_path, "w", encoding="utf-8") as handle:
        for part in parts:
            handle.write("file '" + part.replace("'", "'\\''") + "'\n")
    rc, tail = _run([*FFMPEG_BASE, "-f", "concat", "-safe", "0", "-i", list_path, *AUDIO_ARGS, dst], EXTRACT_TIMEOUT_S)
    if rc != 0 or not os.path.isfile(dst):
        raise PipelineRejection(INPUT_REJECTED, f"audio concatenation failed (ffmpeg exit {rc}: {_last_line(tail, 'no output')}).")
    return probe(dst).duration_s


def _encode_jpeg(image, dst, quality):
    image.save(dst, "JPEG", quality=quality, optimize=True)
    return os.path.getsize(dst)


def extract_frame(src, t_seconds, dst, max_edge=FRAME_MAX_EDGE):
    """One key frame at `t_seconds` of `src`, scaled to `max_edge` on the long side (aspect preserved,
    never enlarged), JPEG quality 85, re-encoded at lower quality until it fits FRAME_MAX_BYTES."""
    tmp_png = dst + ".png"
    rc, tail = _run([*FFMPEG_BASE, "-ss", f"{t_seconds:.3f}", "-i", src, "-frames:v", "1", "-f", "image2", tmp_png], FRAME_TIMEOUT_S)
    if rc != 0 or not os.path.isfile(tmp_png):
        raise FrameExtractionFailed(f"ffmpeg exit {rc} at {t_seconds:.1f}s: {_last_line(tail, 'no output')}")
    try:
        with Image.open(tmp_png) as opened:
            frame = opened.convert("RGB")
        frame.thumbnail((max_edge, max_edge))
        size = _encode_jpeg(frame, dst, FRAME_JPEG_QUALITY)
        for quality in FRAME_FALLBACK_QUALITIES:
            if size <= FRAME_MAX_BYTES:
                break
            size = _encode_jpeg(frame, dst, quality)
        if size > FRAME_MAX_BYTES:
            os.remove(dst)
            raise FrameExtractionFailed(f"frame at {t_seconds:.1f}s is {size} bytes after re-encoding, above {FRAME_MAX_BYTES}")
    finally:
        if os.path.exists(tmp_png):
            os.remove(tmp_png)
