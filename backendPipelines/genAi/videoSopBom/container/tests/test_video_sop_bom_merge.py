# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Transcript segmentation and windowing, then the deterministic merge of per-window extractions.

Run from the container directory:  python -m pytest tests/test_video_sop_bom_merge.py -q

Timestamps are emitted per segment (sentence or 30 s), not per word — per-word prefixes triple the
token count. Windows are 900 s with a 60 s overlap so a step straddling a cut is seen by both windows
and the merge removes the duplicate.
"""

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from conftest import EMPTY_TRANSCRIPT, make_transcript  # noqa: E402


def _words(start, end, step, sentence_every=4):
    """Evenly spaced words from `start` to `end`; every `sentence_every`-th word ends a sentence."""
    words = []
    index = 0
    t = start
    while t < end:
        index += 1
        text = f"word{index}" + ("." if index % sentence_every == 0 else "")
        words.append((t, t + step * 0.8, text))
        t += step
    return words


class TestSegments:
    def test_sentences_close_a_segment_and_punctuation_glues_to_the_previous_word(self):
        from video_sop_bom_pipeline.windows import build_segments

        transcript = make_transcript([(0.5, 0.9, "Remove"), (1.0, 1.4, "the"), (1.5, 2.0, "cover."), (2.5, 3.0, "Next"), (3.1, 3.6, "step.")])
        segments = build_segments(transcript)
        assert [(s.start_s, s.end_s, s.text) for s in segments] == [(0.5, 2.0, "Remove the cover."), (2.5, 3.6, "Next step.")]

    def test_a_segment_is_cut_at_30_seconds_without_punctuation(self):
        from video_sop_bom_pipeline.windows import SEGMENT_MAX_S, build_segments

        words = [(float(t), float(t) + 0.5, f"w{t}") for t in range(0, 70, 5)]  # no sentence ends
        segments = build_segments(make_transcript(words))
        assert SEGMENT_MAX_S == 30.0
        assert [s.start_s for s in segments] == [0.0, 30.0, 60.0]

    def test_empty_transcript_has_no_segments(self):
        from video_sop_bom_pipeline.windows import build_segments, transcript_text

        assert build_segments(EMPTY_TRANSCRIPT) == []
        assert transcript_text(EMPTY_TRANSCRIPT) == ""

    def test_transcript_text_is_one_timestamped_line_per_segment(self):
        from video_sop_bom_pipeline.windows import format_timestamp, transcript_text

        assert format_timestamp(3725.4) == "01:02:05"
        text = transcript_text(make_transcript([(3725.4, 3726.0, "Lift"), (3726.1, 3726.5, "it.")]))
        assert text == "[01:02:05] Lift it.\n"


class TestWindows:
    def test_two_hours_become_nine_overlapping_windows(self):
        from video_sop_bom_pipeline.windows import OVERLAP_S, WINDOW_S, build_windows

        assert (WINDOW_S, OVERLAP_S) == (900, 60)
        windows = build_windows(make_transcript(_words(0, 7200, 10)))
        assert [w.index for w in windows] == list(range(9))
        assert (windows[0].start_s, windows[0].end_s) == (0.0, 900.0)
        assert (windows[1].start_s, windows[1].end_s) == (840.0, 1740.0)
        assert windows[0].centre_s == 450.0
        # A segment starting inside the overlap is in both windows.
        in_overlap = [s for s in windows[0].segments if 840.0 <= s.start_s < 900.0]
        assert in_overlap and all(s in windows[1].segments for s in in_overlap)
        assert windows[0].text.startswith("[00:00:00] word1")

    def test_a_short_transcript_is_one_window(self):
        from video_sop_bom_pipeline.windows import build_windows

        windows = build_windows(make_transcript(_words(0, 150, 10)))
        assert len(windows) == 1 and windows[0].index == 0

    def test_empty_transcript_has_no_windows(self):
        from video_sop_bom_pipeline.windows import build_windows

        assert build_windows(EMPTY_TRANSCRIPT) == []

    def test_custom_sizes_and_the_overlap_guard(self):
        from video_sop_bom_pipeline.windows import build_windows

        windows = build_windows(make_transcript(_words(0, 300, 10)), window_s=100, overlap_s=10)
        assert [(w.start_s, w.end_s) for w in windows] == [(0.0, 100.0), (90.0, 190.0), (180.0, 280.0), (270.0, 370.0)]
        with pytest.raises(ValueError):
            build_windows(make_transcript(_words(0, 300, 10)), window_s=60, overlap_s=60)


def _w(index, start, end):
    from video_sop_bom_pipeline.windows import Window

    return Window(index=index, start_s=float(start), end_s=float(end), segments=[])


def _s(step, t, action, component, deps=None):
    return {
        "step": step, "timestamp_seconds": float(t), "action": action, "component": component, "tools": [], "fasteners": [],
        "locations": [], "dependencies": deps or [], "motion": {"allowed": [], "restricted": []},
        "force": {"amount": None, "indicator": None}, "failure_modes": [], "notes": "",
    }


def _c(description, mass=None, qty=1):
    return {
        "part_level": 1, "part_type": "Enclosure", "part_description": description, "qty": qty,
        "material_or_component_type": "PC/ABS", "mass_g_per_unit": mass, "primary_manufacturing_process": None,
        "first_seen_timestamp_seconds": 1.0,
    }


def _r(steps=(), components=(), moments=(), narration=""):
    return {"steps": list(steps), "components": list(components), "key_moments": list(moments), "product_name_from_narration": narration}


class TestMerge:
    def test_duplicate_steps_across_the_overlap_dedupe_to_the_nearer_window_centre_and_dependencies_remap(self):
        from video_sop_bom_pipeline.merge import merge_windows

        w0, w1 = _w(0, 0, 900), _w(1, 840, 1740)
        r0 = _r(steps=[_s(1, 100, "Remove", "cover"), _s(2, 870.0, "lift the board", "Board")])
        r1 = _r(steps=[_s(1, 872.0, "Lift the board", "board"), _s(2, 1000, "unplug", "cable", deps=[{"step": 1, "text": "board lifted"}])])
        merged = merge_windows([(w0, r0), (w1, r1)])
        assert [s["step"] for s in merged.steps] == [1, 2, 3]
        # |872 - 1290| = 418 beats |870 - 450| = 420, so window 1's copy is kept.
        assert [s["action"] for s in merged.steps] == ["Remove", "Lift the board", "unplug"]
        assert merged.steps[1]["timestamp_seconds"] == 872.0 and merged.steps[1]["source_window"] == 1
        # Window-1 local step 1 is the merged global step 2.
        assert merged.steps[2]["dependencies"] == [{"step": 2, "text": "board lifted"}]
        assert merged.window_count == 2

    def test_steps_more_than_5s_apart_or_on_a_different_component_are_both_kept(self):
        from video_sop_bom_pipeline.merge import STEP_DEDUPE_WINDOW_S, merge_windows

        assert STEP_DEDUPE_WINDOW_S == 5.0
        w0, w1 = _w(0, 0, 900), _w(1, 840, 1740)
        merged = merge_windows([
            (w0, _r(steps=[_s(1, 870.0, "lift", "board"), _s(2, 871.0, "lift", "cover")])),
            (w1, _r(steps=[_s(1, 876.0, "lift", "board")])),
        ])
        assert [(s["action"], s["component"], s["timestamp_seconds"]) for s in merged.steps] == [
            ("lift", "board", 870.0), ("lift", "cover", 871.0), ("lift", "board", 876.0),
        ]

    def test_a_dependency_on_a_step_that_was_never_captured_keeps_its_prose(self):
        from video_sop_bom_pipeline.merge import merge_windows

        merged = merge_windows([(_w(0, 0, 900), _r(steps=[_s(1, 10, "open", "box", deps=[{"step": 9, "text": "power off first"}, {"step": None, "text": "wear gloves"}])]))])
        assert merged.steps[0]["dependencies"] == [{"step": None, "text": "power off first"}, {"step": None, "text": "wear gloves"}]

    def test_components_dedupe_by_normalised_description_and_fill_missing_mass(self):
        from video_sop_bom_pipeline.merge import merge_windows

        merged = merge_windows([
            (_w(0, 0, 900), _r(components=[_c("Rear cover"), _c("Main PCBA", mass=12.0)])),
            (_w(1, 840, 1740), _r(components=[_c("rear  cover.", mass=42.0, qty=2), _c("Screws", mass=0.4, qty=4)])),
        ])
        assert [c["part_description"] for c in merged.components] == ["Rear cover", "Main PCBA", "Screws"]
        rear = merged.components[0]
        assert rear["mass_g_per_unit"] == 42.0 and rear["qty"] == 2

    def test_key_moments_dedupe_within_2s_and_are_sorted(self):
        from video_sop_bom_pipeline.merge import KEY_MOMENT_DEDUPE_S, merge_windows

        assert KEY_MOMENT_DEDUPE_S == 2.0
        moments = [{"timestamp_seconds": t, "reason": "r", "expected_content": "e"} for t in (30.0, 11.5, 10.0, 13.9)]
        merged = merge_windows([(_w(0, 0, 900), _r(moments=moments))])
        assert [m["timestamp_seconds"] for m in merged.key_moments] == [10.0, 13.9, 30.0]

    def test_normalise_text_and_narrated_product_name(self):
        from video_sop_bom_pipeline.merge import merge_windows, normalise_text

        assert normalise_text("  Lift, the BOARD! ") == "lift the board"
        assert normalise_text(None) == ""
        # The fold is vocab.normalize_vocab_value's: a typographic dash and a curly quote are ASCII first.
        assert normalise_text("Thermal–Management ‘pad’") == "thermal management pad"
        merged = merge_windows([(_w(0, 0, 900), _r(narration="")), (_w(1, 840, 1740), _r(narration="In-Sight 2800"))])
        assert merged.product_name_from_narration == "In-Sight 2800"

    def test_empty_results_merge_to_empty(self):
        from video_sop_bom_pipeline.merge import merge_windows

        merged = merge_windows([])
        assert (merged.steps, merged.components, merged.key_moments, merged.window_count) == ([], [], [], 0)
