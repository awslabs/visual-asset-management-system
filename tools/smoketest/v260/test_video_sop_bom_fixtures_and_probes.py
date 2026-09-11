"""Offline tests for the Video SOP/BOM fixture generator and R0 probe script.

Both scripts are imported by the smoke suite (the generator for `STEPS`/`KEYWORDS`, the probes for
their registry), so their module-level contract is pinned here without AWS, ffmpeg or Polly. The
import-time guard matters most: a client built at import would make `python -c "import ..."` a
credentialed call, and the suite imports these on every run.

Run:  python -m pytest tools/smoketest/v260/test_video_sop_bom_fixtures_and_probes.py -q
"""

from __future__ import annotations

import importlib
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _fresh_import(name, monkeypatch):
    """Import `name` with every boto3 constructor replaced by one that fails the test."""
    import boto3  # noqa: PLC0415

    def _boom(*args, **kwargs):
        raise AssertionError(f"AWS client construction at import time: {args} {kwargs}")

    monkeypatch.setattr(boto3, "client", _boom)
    monkeypatch.setattr(boto3, "resource", _boom)
    monkeypatch.setattr(boto3, "Session", _boom)
    sys.modules.pop(name, None)
    return importlib.import_module(name)


class TestFixtureGenerator:
    def test_fresh_import_guard_catches_an_import_time_client(self, tmp_path, monkeypatch):
        """Control for the two `no AWS calls` tests: a module that does build a client at import trips the guard."""
        (tmp_path / "vsb_import_time_client.py").write_text(
            "import boto3\nCLIENT = boto3.client('sts', region_name='us-east-1')\n", encoding="utf-8")
        monkeypatch.syspath_prepend(str(tmp_path))
        with pytest.raises(AssertionError, match="AWS client construction at import time"):
            _fresh_import("vsb_import_time_client", monkeypatch)

    def test_importing_the_generator_makes_no_aws_calls(self, monkeypatch):
        gen = _fresh_import("make_teardown_videos", monkeypatch)
        assert gen.PRODUCT_NAME == "Cognex DataMan 80 barcode reader"

    def test_narration_exposes_keywords_and_steps(self):
        import make_teardown_videos as gen  # noqa: PLC0415
        assert len(gen.KEYWORDS) >= 12
        assert all(k == k.lower() == k.strip() and k.isascii() for k in gen.KEYWORDS)
        assert len(gen.STEPS) >= 8
        assert [s.number for s in gen.STEPS] == list(range(1, len(gen.STEPS) + 1))
        narration = " ".join(s.text for s in gen.STEPS).lower()
        assert all(k in narration for k in gen.KEYWORDS), [k for k in gen.KEYWORDS if k not in narration]
        assert gen.COMPONENT_NOUNS == tuple(s.component for s in gen.STEPS)
        assert all(s.component in s.text.lower() for s in gen.STEPS)
        assert len(set(gen.COMPONENT_NOUNS)) == len(gen.COMPONENT_NOUNS)

    def test_chunk_text_respects_the_polly_limit(self):
        import make_teardown_videos as gen  # noqa: PLC0415
        assert gen.chunk_text("A b. C d. E f.", limit=6) == ["A b.", "C d.", "E f."]
        assert gen.chunk_text("A b. C d. E f.", limit=9) == ["A b. C d.", "E f."]
        assert gen.chunk_text("  one   two.  ") == ["one two."]
        with pytest.raises(ValueError):
            gen.chunk_text("x" * 10 + ".", limit=6)
        assert gen.POLLY_MAX_CHARS == 3000
        assert all(len(gen.chunk_text(s.text)) == 1 for s in gen.STEPS)

    def test_fixture_file_names_match_d13(self):
        import make_teardown_videos as gen  # noqa: PLC0415
        assert gen.HAPPY_PAIR == ("teardown-part1.mp4", "teardown-part2.MP4")
        assert gen.TINY_CLIPS == tuple(f"tiny-{i}.mp4" for i in range(1, 6))
        assert gen.LONG_CLIP == "teardown-long-200s.mp4" and gen.LONG_CLIP_SECONDS == 200 > 180
        assert gen.SIZE_PAIR == ("size-small.mp4", "size-big.mp4")
        assert gen.SILENT_CLIP == "silent.mp4" and gen.SILENT_CLIP_SECONDS == 30
        assert gen.SILENT_FLAC == "silence-30s.flac" and gen.SILENT_FLAC_SECONDS == 30
        expected = set(gen.HAPPY_PAIR) | set(gen.TINY_CLIPS) | {gen.LONG_CLIP, gen.SILENT_CLIP, gen.SILENT_FLAC} | set(gen.SIZE_PAIR)
        assert set(gen.FIXTURE_DESCRIPTIONS) == expected and len(expected) == 12
        # The role map is what the smoke suite reads (`FIXTURE_FILES[role]`); every file has exactly one role.
        assert gen.FIXTURE_ROLES == ("happy", "tiny", "long", "size_small", "size_big", "silent", "probe_flac")
        assert tuple(gen.FIXTURE_FILES) == gen.FIXTURE_ROLES
        assert gen.FIXTURE_FILES["happy"] == list(gen.HAPPY_PAIR) and gen.FIXTURE_FILES["tiny"] == list(gen.TINY_CLIPS)
        assert gen.FIXTURE_FILES["long"] == gen.LONG_CLIP and gen.FIXTURE_FILES["probe_flac"] == gen.SILENT_FLAC
        assert (gen.FIXTURE_FILES["size_small"], gen.FIXTURE_FILES["size_big"]) == gen.SIZE_PAIR
        flattened = [n for v in gen.FIXTURE_FILES.values() for n in ([v] if isinstance(v, str) else v)]
        assert sorted(flattened) == sorted(expected) and len(flattened) == 12
        assert gen.DEFAULT_OUT == os.path.join(HERE, "_video_sop_bom_fixtures")
        # Owner decision 7: a no-speech input is rejected on the task token, so the silent fixture
        # exercises that rejection rather than an empty-deliverable success.
        assert "VideoSopBomInputRejected" in gen.FIXTURE_DESCRIPTIONS[gen.SILENT_CLIP]

    def test_frame_captions_maps_seconds_to_steps(self):
        import make_teardown_videos as gen  # noqa: PLC0415
        assert gen.frame_captions(["S1", "S2"], [2.0, 3.0]) == ["S1", "S1", "S2", "S2", "S2"]
        assert gen.frame_captions(["S1"], [1.5]) == ["S1", "S1"]
        assert gen.frame_captions(["S1", "S2"], [0.4, 0.4]) == ["S1"]

    def test_part_scripts_split_the_steps(self):
        import make_teardown_videos as gen  # noqa: PLC0415
        assert gen.PART_STEPS[1] + gen.PART_STEPS[2] == gen.STEPS
        assert len(gen.PART_STEPS[1]) >= 4 and len(gen.PART_STEPS[2]) >= 4
        assert gen.PART_STEPS[1][0].text.startswith("Step one.")


# --- end of fixture-and-probe tests ---
