# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The concatenated-audio timeline: which video a global Transcribe timestamp falls in, and where."""


def build_timeline(tracks):
    """`tracks` are the extracted audio tracks in playback order (`video_key`, `duration` in seconds).
    Offsets come from the extracted audio, not the container duration, so they match what Transcribe timed."""
    entries = []
    keys = []
    cumulative = 0.0
    for index, track in enumerate(tracks):
        duration = float(track["duration"])
        if duration <= 0:
            raise ValueError(f"track {index} ({track.get('video_key')}) has a non-positive duration {duration}")
        entries.append({
            "index": index,
            "video_key": track["video_key"],
            "start_offset": cumulative,
            "end_offset": cumulative + duration,
            "duration": duration,
        })
        keys.append(track["video_key"])
        cumulative += duration
    return {"video_keys": keys, "video_timeline": entries, "total_duration": cumulative}


def map_global_to_local(timeline, t):
    """(video_index, local_seconds) for a global timestamp. Half-open intervals: a boundary belongs to the
    next video; overflow lands 0.1 s before the end of the last video; a negative value is the first video's start."""
    entries = timeline["video_timeline"]
    if not entries:
        raise ValueError("empty timeline")
    if t < 0:
        return entries[0]["index"], 0.0
    for entry in entries:
        if entry["start_offset"] <= t < entry["end_offset"]:
            return entry["index"], t - entry["start_offset"]
    last = entries[-1]
    return last["index"], max(0.0, last["duration"] - 0.1)
