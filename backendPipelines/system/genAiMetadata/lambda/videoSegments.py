#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Fixed time windows of a video for per-window analysis: the window plan, the segment keys and the labels.

The key builder restates ``backend/backend/common/indexing/documentIds.build_video_segment_key`` (a pipeline
code asset cannot import the backend package); the test suite pins the two equal. The media branch image
carries a byte-identical copy of this module, so it imports only the standard library.
"""

import math
from typing import Optional

# Most windows one video is split into; a longer video is sampled coarser rather than cut off.
VIDEO_SEGMENT_MAX = 360
# Smallest interval the template tag VIDEO_SEGMENT_SECONDS turns the per-window analysis on with.
VIDEO_SEGMENT_MIN_SECONDS = 2
# The segment kind of a window document (documentIds.SEGMENT_KINDS).
SEGMENT_KIND = "videoTime"
PLAN_SCHEMA_VERSION = 1

_KEY_PREFIX = "t"
_KEY_DIGITS = 10
_LABEL_SEPARATOR = "\u2013"


def build_video_segment_key(start_ms: int) -> str:
    """``t`` and the window's start millisecond zero-padded to ten digits (``t0000083456``), so the keys of one
    version sort by start time."""
    value = int(start_ms)
    if value < 0:
        raise ValueError(f"segment start millisecond must not be negative, got {value}")
    if value >= 10 ** _KEY_DIGITS:
        raise ValueError(f"segment start millisecond {value} does not fit {_KEY_DIGITS} digits")
    return f"{_KEY_PREFIX}{value:0{_KEY_DIGITS}d}"


def _clock(ms: int) -> str:
    hours, rest = divmod(int(ms), 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def segment_label(start_ms: int, end_ms: int) -> str:
    """``HH:MM:SS.mmm–HH:MM:SS.mmm`` for the window ``[start, end)``."""
    return f"{_clock(start_ms)}{_LABEL_SEPARATOR}{_clock(end_ms)}"


def plan_segments(duration_seconds: float, segment_seconds: int) -> Optional[dict]:
    """The window plan for a video, or ``None`` when no per-window analysis applies: the interval is below
    VIDEO_SEGMENT_MIN_SECONDS, the duration is unknown or not positive, or the video is shorter than two
    intervals (the whole-file vector suffices). ``count = min(VIDEO_SEGMENT_MAX, ceil(duration / seconds))``
    windows of ``duration / count`` seconds each, so a long video is sampled coarser rather than cut off."""
    try:
        duration = float(duration_seconds)
        seconds = int(segment_seconds)
    except (TypeError, ValueError):
        return None
    if seconds < VIDEO_SEGMENT_MIN_SECONDS or not duration > 0 or duration < 2 * seconds:
        return None
    count = min(VIDEO_SEGMENT_MAX, math.ceil(duration / seconds))
    effective = duration / count
    total_ms = int(round(duration * 1000))
    segments = []
    for index in range(count):
        start_ms = int(round(index * effective * 1000))
        end_ms = total_ms if index == count - 1 else int(round((index + 1) * effective * 1000))
        segments.append({
            "segmentKey": build_video_segment_key(start_ms),
            "index": index,
            "startMs": start_ms,
            "endMs": end_ms,
            "label": segment_label(start_ms, end_ms),
        })
    return {
        "schemaVersion": PLAN_SCHEMA_VERSION,
        "videoSegmentSeconds": seconds,
        "effectiveIntervalSeconds": round(effective, 3),
        "durationSeconds": round(duration, 3),
        "count": count,
        "segments": segments,
    }
