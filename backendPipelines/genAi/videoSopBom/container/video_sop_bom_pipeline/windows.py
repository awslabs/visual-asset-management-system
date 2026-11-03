# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Transcript items -> timestamped segments -> overlapping analysis windows."""

from dataclasses import dataclass, field

WINDOW_S = 900
OVERLAP_S = 60
SEGMENT_MAX_S = 30.0
SENTENCE_END = (".", "?", "!")


@dataclass
class Segment:
    start_s: float
    end_s: float
    text: str


@dataclass
class Window:
    index: int
    start_s: float
    end_s: float
    segments: list = field(default_factory=list)

    @property
    def centre_s(self):
        return (self.start_s + self.end_s) / 2.0

    @property
    def text(self):
        return "\n".join(f"[{format_timestamp(segment.start_s)}] {segment.text}" for segment in self.segments)


def format_timestamp(seconds):
    total = int(seconds)
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def _items(transcript_json):
    return (transcript_json.get("results") or {}).get("items") or []


def build_segments(transcript_json):
    """Group pronunciation items into sentences (closed at . ? !) of at most SEGMENT_MAX_S seconds."""
    segments = []
    words = []
    start = end = 0.0

    def flush():
        nonlocal words
        if words:
            segments.append(Segment(start_s=start, end_s=end, text=" ".join(words)))
        words = []

    for item in _items(transcript_json):
        alternatives = item.get("alternatives") or [{}]
        content = alternatives[0].get("content", "")
        if item.get("type") == "pronunciation":
            t0 = float(item.get("start_time", 0.0))
            t1 = float(item.get("end_time", t0))
            if words and t0 - start >= SEGMENT_MAX_S:
                flush()
            if not words:
                start = t0
            words.append(content)
            end = t1
        elif item.get("type") == "punctuation" and words:
            words[-1] = words[-1] + content
            if content in SENTENCE_END:
                flush()
    flush()
    return segments


def transcript_text(transcript_json):
    """The readable transcript.txt: one `[HH:MM:SS] sentence` line per segment; empty for no speech."""
    segments = build_segments(transcript_json)
    if not segments:
        return ""
    return "".join(f"[{format_timestamp(segment.start_s)}] {segment.text}\n" for segment in segments)


def build_windows(transcript_json, window_s=WINDOW_S, overlap_s=OVERLAP_S):
    """Windows of `window_s` seconds starting every `window_s - overlap_s`; a segment belongs to every
    window whose span contains its start, so segments in an overlap appear twice (the merge dedupes)."""
    stride = window_s - overlap_s
    if stride <= 0:
        raise ValueError(f"window_s ({window_s}) must exceed overlap_s ({overlap_s})")
    segments = build_segments(transcript_json)
    if not segments:
        return []
    total = max(segment.end_s for segment in segments)
    windows = []
    start = 0.0
    while True:
        end = start + window_s
        members = [segment for segment in segments if start <= segment.start_s < end]
        if members:
            windows.append(Window(index=len(windows), start_s=start, end_s=end, segments=members))
        if end >= total:
            break
        start += stride
    return windows
