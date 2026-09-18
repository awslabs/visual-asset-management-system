#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The window plan of a video: fixed intervals from the template tag, at most 360 windows (a long video is
sampled coarser, never cut off), none for a video shorter than two intervals, keys that are the start
millisecond zero-padded to ten digits and labels in clock form.

The key builder is restated from the backend's documentIds module (a pipeline code asset cannot import the
backend package) and pinned equal to it by loading that module by path, so a key shape changed on one side
alone fails here rather than as vectors the store groups under the wrong version."""

import importlib.util
import math
import os

import pytest

import sysgenai_harness as h

vs = h.load_local("videoSegments")

_DOCUMENT_IDS = os.path.join(h.REPO_ROOT, "backend", "backend", "common", "indexing", "documentIds.py")


def _backend_document_ids():
    assert os.path.isfile(_DOCUMENT_IDS), (
        f"{_DOCUMENT_IDS} is missing: the shared identity helpers have not landed; the segment key "
        "this pipeline writes must match the store's sort-key builder")
    spec = importlib.util.spec_from_file_location("sysgenai_backend_documentIds", _DOCUMENT_IDS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "build_video_segment_key"), (
        "documentIds.py has no build_video_segment_key: the segment key builders have not landed")
    return module


@pytest.mark.unit
class TestKeys:
    def test_key_is_pinned_to_the_backend_builder(self):
        backend = _backend_document_ids()
        for start_ms in (0, 1, 9_248, 83_456, 3_599_999, 9_999_999_999):
            assert vs.build_video_segment_key(start_ms) == backend.build_video_segment_key(start_ms), start_ms
        for bad in (-1, 10_000_000_000):
            with pytest.raises(ValueError):
                vs.build_video_segment_key(bad)
            with pytest.raises(ValueError):
                backend.build_video_segment_key(bad)

    def test_key_shape_sorts_naturally(self):
        keys = [vs.build_video_segment_key(ms) for ms in (0, 9_248, 18_496, 92_480, 3_600_000)]
        assert keys[0] == "t0000000000" and keys[1] == "t0000009248"
        assert keys == sorted(keys)
        assert all(len(key) == 11 for key in keys)
        cap = _backend_document_ids().SEGMENT_KEY_MAX_BYTES
        assert all(len(key.encode("utf-8")) <= cap for key in keys) and cap == 32

    def test_kind_is_a_registry_segment_kind(self):
        backend = _backend_document_ids()
        assert vs.SEGMENT_KIND == "videoTime"
        assert vs.SEGMENT_KIND in backend.SEGMENT_KINDS


@pytest.mark.unit
class TestLabel:
    def test_label_format(self):
        assert vs.segment_label(0, 10_000) == "00:00:00.000\u201300:00:10.000"
        assert vs.segment_label(3_723_456, 3_733_456) == "01:02:03.456\u201301:02:13.456"
        assert vs.segment_label(83_456, 92_480) == "00:01:23.456\u201300:01:32.480"


@pytest.mark.unit
class TestPlan:
    def test_constants(self):
        assert vs.VIDEO_SEGMENT_MAX == 360
        assert vs.VIDEO_SEGMENT_MIN_SECONDS == 2
        assert vs.PLAN_SCHEMA_VERSION == 1

    def test_no_plan_below_the_minimum_interval(self):
        for seconds in (1, 0, -3, "x", None):
            assert vs.plan_segments(600.0, seconds) is None, seconds

    def test_no_plan_for_a_video_shorter_than_two_intervals(self):
        assert vs.plan_segments(19.99, 10) is None
        assert vs.plan_segments(20.0, 10)["count"] == 2

    def test_zero_negative_or_unknown_duration_has_no_plan(self):
        for duration in (0, -5.0, None, "n/a", float("nan")):
            assert vs.plan_segments(duration, 10) is None, duration

    def test_plan_shape_and_windows(self):
        plan = vs.plan_segments(92.48, 10)
        assert plan["schemaVersion"] == 1
        assert plan["videoSegmentSeconds"] == 10 and plan["durationSeconds"] == 92.48
        assert plan["count"] == 10 and plan["effectiveIntervalSeconds"] == 9.248
        assert list(plan) == ["schemaVersion", "videoSegmentSeconds", "effectiveIntervalSeconds", "durationSeconds",
                              "count", "segments"]
        segments = plan["segments"]
        assert len(segments) == 10
        assert segments[0] == {"segmentKey": "t0000000000", "index": 0, "startMs": 0, "endMs": 9248,
                               "label": "00:00:00.000\u201300:00:09.248"}
        assert segments[-1]["startMs"] == 83_232 and segments[-1]["endMs"] == 92_480 and segments[-1]["index"] == 9
        for segment in segments:
            assert segment["segmentKey"] == vs.build_video_segment_key(segment["startMs"])
            assert segment["label"] == vs.segment_label(segment["startMs"], segment["endMs"])
            assert set(segment) == {"segmentKey", "index", "startMs", "endMs", "label"}

    def test_a_long_video_is_sampled_coarser(self):
        plan = vs.plan_segments(36_000.0, 10)
        assert plan["count"] == 360 and plan["effectiveIntervalSeconds"] == 100.0
        assert plan["segments"][-1]["endMs"] == 36_000_000
        assert len({segment["segmentKey"] for segment in plan["segments"]}) == 360

    @pytest.mark.parametrize("duration,seconds", [(61.0, 2), (3599.5, 5), (100_000.0, 3)])
    def test_windows_are_contiguous_and_keys_unique(self, duration, seconds):
        plan = vs.plan_segments(duration, seconds)
        segments = plan["segments"]
        assert plan["count"] == len(segments) == min(vs.VIDEO_SEGMENT_MAX, math.ceil(duration / seconds))
        assert segments[0]["startMs"] == 0 and segments[-1]["endMs"] == int(round(duration * 1000))
        for earlier, later in zip(segments, segments[1:]):
            assert earlier["endMs"] == later["startMs"]
        assert all(segment["endMs"] > segment["startMs"] for segment in segments)
        assert len({segment["segmentKey"] for segment in segments}) == len(segments)
