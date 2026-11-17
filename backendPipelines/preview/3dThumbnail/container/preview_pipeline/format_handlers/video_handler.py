# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Video frame sampler for the media previews, on PyAV (its wheels bundle the FFmpeg libraries; the
image installs no Debian ffmpeg).

A video is untrusted input. It is opened as a local file only (``protocol_whitelist=file``: no network,
no ``concat``/HLS/playlist indirection to other URLs), through a short allowlist of container formats
rather than whatever FFmpeg's probe finds, and read within a time limit and a frame cap. Frames are
taken at evenly spaced timestamps by seeking, so a long video costs a handful of seeks, not a full
decode.
"""

import time
from typing import List, Optional, Tuple

from PIL import Image

from ..utils import image_utils
from ..utils.logging import get_logger
from .image_handler import MediaError

logger = get_logger()

# The accepted video input extensions. Drift-guarded against the media workflow's filters.
SUPPORTED_EXTENSIONS = frozenset({
    ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".mpg", ".mpeg", ".mts", ".m2ts",
})

# FFmpeg demuxer names the pipeline opens, by extension first and then the rest: the file is opened
# with an explicit format, so FFmpeg never probes for an unlisted one (``concat``, ``hls``, ``image2``,
# a device or a protocol). A file whose content is another allowed format still opens.
_FORMATS_BY_EXTENSION = {
    ".mp4": ("mov",), ".m4v": ("mov",), ".mov": ("mov",),
    ".mkv": ("matroska",), ".webm": ("matroska",),
    ".avi": ("avi",), ".wmv": ("asf",),
    ".mpg": ("mpeg", "mpegts"), ".mpeg": ("mpeg", "mpegts"),
    ".mts": ("mpegts",), ".m2ts": ("mpegts",),
}
ALLOWED_FORMATS = ("mov", "matroska", "avi", "asf", "mpeg", "mpegts")
CONTAINER_OPTIONS = {"protocol_whitelist": "file"}

# Frames sampled across the video (default and ceiling), the long edge they are scaled to, and the
# frame rate the preview GIF plays at.
DEFAULT_SAMPLE_FRAMES = 12
MAX_SAMPLE_FRAMES = 24
VIDEO_LONG_EDGE = 480
GIF_FRAME_MS = 250  # about 4 frames a second
# Decoding stops after this long, with whatever frames it has; a file that yields none fails.
DECODE_TIME_LIMIT_SECONDS = 600.0
# Frames decoded after a seek before the sample is taken from what was reached, and frames kept when
# a video reports no duration and is read from the start.
MAX_FRAMES_PER_SEEK = 300
MAX_SEQUENTIAL_FRAMES = 300


class NoVideoStream(MediaError):
    """The file holds no decodable video frames (an audio-only file, or a video stream with none)."""


def can_handle(ext: str) -> bool:
    return (ext or "").lower() in SUPPORTED_EXTENSIONS


def _open(path: str, ext: str):
    """The file opened with the first allowed format that reads it."""
    import av
    candidates = list(_FORMATS_BY_EXTENSION.get(ext.lower(), ()))
    candidates += [name for name in ALLOWED_FORMATS if name not in candidates]
    last_error: Optional[Exception] = None
    for name in candidates:
        try:
            container = av.open(path, mode="r", format=name, options=dict(CONTAINER_OPTIONS))
        except Exception as e:  # not this format; try the next
            last_error = e
            continue
        if not any(part in ALLOWED_FORMATS for part in container.format.name.split(",")):
            container.close()
            continue
        return container
    detail = f" ({type(last_error).__name__}: {str(last_error)[:160]})" if last_error else ""
    raise MediaError(f"The video file ({ext.lstrip('.').upper()}) could not be opened as a supported "
                     f"video container{detail}")


def _rotate_upright(image: Image.Image, rotation) -> Image.Image:
    """The frame rotated by its display-matrix rotation (degrees counter-clockwise, as FFmpeg
    reports it) so a phone video recorded in portrait is upright."""
    try:
        degrees = int(round(float(rotation))) % 360 if rotation is not None else 0
    except (TypeError, ValueError):
        return image
    if degrees == 0:
        return image
    if degrees % 90 == 0:
        return image.rotate(degrees, expand=True)
    return image.rotate(degrees, expand=True, fillcolor=(0, 0, 0))


def _to_image(frame, long_edge: int) -> Image.Image:
    """A decoded frame as an upright RGB image whose long edge is at most ``long_edge``."""
    width, height = frame.width, frame.height
    longest = max(width, height)
    if longest > long_edge:
        scale = long_edge / longest
        width, height = max(2, int(round(width * scale))), max(2, int(round(height * scale)))
    image = frame.to_image(width=width, height=height) if (width, height) != (frame.width, frame.height) \
        else frame.to_image()
    return _rotate_upright(image.convert("RGB"), getattr(frame, "rotation", 0))


def _duration_seconds(container, stream) -> Optional[float]:
    import av
    if container.duration and container.duration > 0:
        return container.duration / av.time_base
    if stream.duration and stream.time_base:
        seconds = float(stream.duration * stream.time_base)
        return seconds if seconds > 0 else None
    return None


def sample_frames(path: str, ext: str, count: int = DEFAULT_SAMPLE_FRAMES,
                  long_edge: int = VIDEO_LONG_EDGE,
                  time_limit: float = DECODE_TIME_LIMIT_SECONDS,
                  clock=time.monotonic) -> Tuple[List[Image.Image], Optional[float]]:
    """Up to ``count`` upright RGB frames (long edge at most ``long_edge``) taken evenly across the
    video, and its duration in seconds when known. Raises NoVideoStream when the file holds no
    decodable video frames and MediaError when it cannot be opened."""
    count = max(1, min(int(count), MAX_SAMPLE_FRAMES))
    started = clock()
    container = _open(path, ext)
    try:
        streams = [s for s in container.streams.video
                   if not (s.disposition & 0x0400)]  # skip an attached cover picture
        if not streams:
            raise NoVideoStream(f"No video stream in '{path.rsplit('/', 1)[-1]}'; no preview written.")
        stream = streams[0]
        stream.thread_type = "AUTO"
        duration = _duration_seconds(container, stream)
        frames: List[Image.Image] = []
        try:
            if duration:
                frames = _seek_sample(container, stream, duration, count, long_edge, started,
                                      time_limit, clock)
            if not frames:
                frames = _sequential_sample(container, stream, count, long_edge, started, time_limit,
                                            clock)
        except MediaError:
            raise
        except Exception as e:
            if not frames:
                raise MediaError(f"The video ({ext.lstrip('.').upper()}) could not be decoded "
                                 f"({type(e).__name__}: {str(e)[:160]})") from e
            logger.warning(f"Video decoding stopped early ({type(e).__name__}); using {len(frames)} frames")
        if not frames:
            raise NoVideoStream(f"No video stream in '{path.rsplit('/', 1)[-1]}'; no preview written.")
        return frames, duration
    finally:
        container.close()


def _seek_sample(container, stream, duration: float, count: int, long_edge: int, started: float,
                 time_limit: float, clock) -> List[Image.Image]:
    import av
    frames: List[Image.Image] = []
    for i in range(count):
        if clock() - started > time_limit:
            logger.warning(f"Video decode time limit reached after {len(frames)} frames")
            break
        target = duration * (i + 0.5) / count
        container.seek(int(target * av.time_base), any_frame=False, backward=True)
        chosen = None
        decoded = 0
        try:
            for frame in container.decode(stream):
                decoded += 1
                chosen = frame
                if frame.time is None or frame.time >= target or decoded >= MAX_FRAMES_PER_SEEK:
                    break
        except Exception as e:
            logger.info(f"Frame at {target:.1f} s not decoded ({type(e).__name__})")
        if chosen is not None:
            frames.append(_to_image(chosen, long_edge))
    return frames


def _sequential_sample(container, stream, count: int, long_edge: int, started: float,
                       time_limit: float, clock) -> List[Image.Image]:
    """Frames read from the start (a video with no usable duration or index, or one the seeks found
    nothing in): every frame up to MAX_SEQUENTIAL_FRAMES is a candidate and ``count`` evenly spaced
    ones are kept."""
    container.seek(0)
    decoded = []
    for frame in container.decode(stream):
        decoded.append(_to_image(frame, long_edge))
        if len(decoded) >= MAX_SEQUENTIAL_FRAMES or clock() - started > time_limit:
            break
    return [decoded[i] for i in image_utils.sample_frames(len(decoded), count)] if decoded else []
