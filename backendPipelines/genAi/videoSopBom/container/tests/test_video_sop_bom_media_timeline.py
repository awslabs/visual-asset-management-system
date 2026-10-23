# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""ffmpeg/ffprobe through the `media._run` seam, and the multi-video timeline.

Run from the container directory:  python -m pytest tests/test_video_sop_bom_media_timeline.py -q

The guard test at the top keeps the fakes honest: every name the fakes replace must still exist on the
real module, and the real `_run` must actually execute a subprocess. Without it a renamed seam would
leave every other test here green against code the container no longer runs.
"""

import os
import sys

import pytest
from PIL import Image

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from conftest import fake_run_factory  # noqa: E402

SEAM_NAMES = ("_run", "probe", "extract_audio_flac", "concat_flac", "extract_frame")


def _contains(argv, pair):
    """True when `pair` appears as consecutive argv elements."""
    for index in range(len(argv) - len(pair) + 1):
        if list(argv[index:index + len(pair)]) == list(pair):
            return True
    return False


class TestMediaSeamGuard:
    def test_the_real_module_defines_every_seam_name(self):
        from video_sop_bom_pipeline import media

        for name in SEAM_NAMES:
            assert callable(getattr(media, name, None)), f"media.{name} is missing; the fakes replace a name that no longer exists"

    def test_the_real_run_executes_a_subprocess_and_bounds_the_tail(self):
        from video_sop_bom_pipeline import media

        assert media._run([sys.executable, "-c", "print('probe-ok')"], 30) == (0, "probe-ok")
        rc, _ = media._run([sys.executable, "-c", "import sys; sys.exit(3)"], 30)
        assert rc == 3
        rc, tail = media._run([sys.executable, "-c", "print('\\n'.join(str(i) for i in range(1000)))"], 30)
        assert rc == 0
        lines = tail.splitlines()
        assert len(lines) == media.TAIL_LINES == 400
        assert lines[-1] == "999"

    def test_missing_binary_and_timeout_are_return_codes_not_exceptions(self):
        from video_sop_bom_pipeline import media

        rc, tail = media._run(["definitely-not-a-binary-vsb"], 5)
        assert rc == 127 and "not found" in tail
        rc, tail = media._run([sys.executable, "-c", "import time; time.sleep(10)"], 1)
        assert rc == 124 and "timed out" in tail


class TestProbe:
    def test_duration_and_audio_flag_come_from_ffprobe_json(self, monkeypatch):
        from video_sop_bom_pipeline import media

        fake = fake_run_factory({"part1.mp4": 75.5})
        monkeypatch.setattr(media, "_run", fake)
        result = media.probe("/work/input/part1.mp4")
        assert result.duration_s == 75.5
        assert result.has_audio is True
        assert [s["codec_type"] for s in result.streams] == ["video", "audio"]
        argv = fake.calls[0]
        assert argv[0] == "ffprobe" and argv[-1] == "/work/input/part1.mp4"
        assert _contains(argv, ["-of", "json"])

    def test_a_file_without_an_audio_stream_is_reported(self, monkeypatch):
        from video_sop_bom_pipeline import media

        monkeypatch.setattr(media, "_run", fake_run_factory(has_audio=False))
        assert media.probe("/work/input/silent.mp4").has_audio is False

    def test_a_non_zero_ffprobe_exit_is_an_input_rejection_never_a_zero_duration(self, monkeypatch):
        from video_sop_bom_pipeline import media
        from video_sop_bom_pipeline.errors import PipelineRejection

        monkeypatch.setattr(media, "_run", fake_run_factory(fail_basenames=("broken.mp4",)))
        with pytest.raises(PipelineRejection) as raised:
            media.probe("/work/input/broken.mp4")
        assert raised.value.code == "VideoSopBomInputRejected"
        assert "broken.mp4" in raised.value.cause
        assert "ffprobe exit 1" in raised.value.cause

    def test_unparseable_ffprobe_output_is_a_rejection(self, monkeypatch):
        from video_sop_bom_pipeline import media
        from video_sop_bom_pipeline.errors import PipelineRejection

        monkeypatch.setattr(media, "_run", lambda argv, timeout: (0, "not json"))
        with pytest.raises(PipelineRejection) as raised:
            media.probe("/work/input/x.mp4")
        assert raised.value.code == "VideoSopBomInputRejected"


class TestAudio:
    def test_extract_audio_flac_is_mono_16k_flac_and_returns_the_track_duration(self, monkeypatch, tmp_path):
        from video_sop_bom_pipeline import media

        fake = fake_run_factory({"0.flac": 75.0})
        monkeypatch.setattr(media, "_run", fake)
        dst = str(tmp_path / "0.flac")
        assert media.extract_audio_flac(str(tmp_path / "part1.mp4"), dst) == 75.0
        argv = fake.calls[0]
        assert argv[0] == "ffmpeg" and argv[-1] == dst
        for pair in (["-vn"], ["-ac", "1"], ["-ar", "16000"], ["-c:a", "flac"]):
            assert _contains(argv, pair), argv
        assert os.path.isfile(dst)
        assert fake.calls[1][0] == "ffprobe", "the duration comes from probing the extracted track"

    def test_extraction_failure_names_the_input(self, monkeypatch, tmp_path):
        from video_sop_bom_pipeline import media
        from video_sop_bom_pipeline.errors import PipelineRejection

        monkeypatch.setattr(media, "_run", fake_run_factory(fail_basenames=("0.flac",)))
        with pytest.raises(PipelineRejection) as raised:
            media.extract_audio_flac(str(tmp_path / "part1.mp4"), str(tmp_path / "0.flac"))
        assert raised.value.code == "VideoSopBomInputRejected"
        assert "part1.mp4" in raised.value.cause and "audio extraction failed" in raised.value.cause

    def test_concat_flac_writes_a_list_file_and_re_encodes(self, monkeypatch, tmp_path):
        from video_sop_bom_pipeline import media

        parts = [str(tmp_path / "0.flac"), str(tmp_path / "1.flac")]
        for part in parts:
            with open(part, "wb") as handle:
                handle.write(b"fLaC")
        fake = fake_run_factory({"combined.flac": 155.0})
        monkeypatch.setattr(media, "_run", fake)
        dst = str(tmp_path / "combined.flac")
        assert media.concat_flac(parts, dst) == 155.0
        argv = fake.calls[0]
        assert _contains(argv, ["-f", "concat"]) and _contains(argv, ["-safe", "0"]) and _contains(argv, ["-c:a", "flac"])
        list_path = argv[argv.index("-i") + 1]
        with open(list_path, encoding="utf-8") as handle:
            assert handle.read().splitlines() == [f"file '{parts[0]}'", f"file '{parts[1]}'"]


class TestFrames:
    def _spy_encoder(self, monkeypatch, media):
        qualities = []
        real = media._encode_jpeg

        def _spy(image, dst, quality):
            qualities.append(quality)
            return real(image, dst, quality)

        monkeypatch.setattr(media, "_encode_jpeg", _spy)
        return qualities

    def test_landscape_frame_is_scaled_to_1568_long_edge_at_quality_85(self, monkeypatch, tmp_path):
        from video_sop_bom_pipeline import media

        fake = fake_run_factory(png_size=(1920, 1080))
        monkeypatch.setattr(media, "_run", fake)
        qualities = self._spy_encoder(monkeypatch, media)
        dst = str(tmp_path / "keyframe-0000-00h01m15s.jpg")
        media.extract_frame(str(tmp_path / "part1.mp4"), 75.0, dst)
        with Image.open(dst) as image:
            assert image.format == "JPEG" and image.size == (1568, 882)
        assert qualities == [85]
        assert not os.path.exists(dst + ".png"), "the intermediate PNG is removed"
        argv = fake.calls[0]
        assert argv[0] == "ffmpeg" and _contains(argv, ["-ss", "75.000"]) and _contains(argv, ["-frames:v", "1"])
        assert argv.index("-ss") < argv.index("-i"), "seek before the input for a fast keyframe seek"

    def test_portrait_keeps_aspect_and_small_frames_are_not_enlarged(self, monkeypatch, tmp_path):
        from video_sop_bom_pipeline import media

        monkeypatch.setattr(media, "_run", fake_run_factory(png_size=(1080, 1920)))
        media.extract_frame(str(tmp_path / "v.mp4"), 1.0, str(tmp_path / "a.jpg"))
        with Image.open(str(tmp_path / "a.jpg")) as image:
            assert image.size == (882, 1568)
        monkeypatch.setattr(media, "_run", fake_run_factory(png_size=(640, 480)))
        media.extract_frame(str(tmp_path / "v.mp4"), 1.0, str(tmp_path / "b.jpg"))
        with Image.open(str(tmp_path / "b.jpg")) as image:
            assert image.size == (640, 480)

    def test_an_oversize_frame_is_re_encoded_at_lower_quality_then_skipped(self, monkeypatch, tmp_path):
        from video_sop_bom_pipeline import media

        monkeypatch.setattr(media, "_run", fake_run_factory(png_size=(1920, 1080)))
        monkeypatch.setattr(media, "FRAME_MAX_BYTES", 1)
        qualities = self._spy_encoder(monkeypatch, media)
        dst = str(tmp_path / "big.jpg")
        with pytest.raises(media.FrameExtractionFailed) as raised:
            media.extract_frame(str(tmp_path / "v.mp4"), 1.0, dst)
        assert qualities == [85, 75, 65, 50]
        assert not os.path.exists(dst)
        assert "bytes" in str(raised.value)

    def test_ffmpeg_failure_is_a_skippable_frame_failure_not_a_rejection(self, monkeypatch, tmp_path):
        from video_sop_bom_pipeline import media
        from video_sop_bom_pipeline.errors import PipelineRejection

        monkeypatch.setattr(media, "_run", lambda argv, timeout: (1, "ffmpeg: invalid seek"))
        with pytest.raises(media.FrameExtractionFailed):
            media.extract_frame(str(tmp_path / "v.mp4"), 1.0, str(tmp_path / "c.jpg"))
        assert not issubclass(media.FrameExtractionFailed, PipelineRejection)


class TestTimeline:
    def _timeline(self):
        from video_sop_bom_pipeline.timeline import build_timeline

        return build_timeline([{"video_key": "/part1.mp4", "duration": 75.0}, {"video_key": "/part2.MP4", "duration": 80.0}])

    def test_offsets_are_cumulative_and_the_document_matches_the_shipped_schema(self):
        import jsonschema

        from video_sop_bom_pipeline import load_schema

        timeline = self._timeline()
        assert timeline["video_keys"] == ["/part1.mp4", "/part2.MP4"]
        assert timeline["total_duration"] == 155.0
        assert timeline["video_timeline"][0] == {"index": 0, "video_key": "/part1.mp4", "start_offset": 0.0, "end_offset": 75.0, "duration": 75.0}
        assert timeline["video_timeline"][1]["start_offset"] == 75.0
        jsonschema.validate(timeline, load_schema("timeline_schema"))

    def test_interior_boundary_overflow_and_negative(self):
        from video_sop_bom_pipeline.timeline import map_global_to_local

        timeline = self._timeline()
        assert map_global_to_local(timeline, 10.0) == (0, 10.0)
        assert map_global_to_local(timeline, 75.0) == (1, 0.0), "a boundary maps to the next video at 0.0 (half-open)"
        assert map_global_to_local(timeline, 100.0) == (1, 25.0)
        index, local = map_global_to_local(timeline, 500.0)
        assert index == 1 and local == pytest.approx(79.9), "overflow lands near the end of the last video"
        assert map_global_to_local(timeline, -3.0) == (0, 0.0), "a negative timestamp is the first video's start, not the last video's end"

    def test_a_non_positive_duration_is_refused(self):
        from video_sop_bom_pipeline.timeline import build_timeline

        with pytest.raises(ValueError):
            build_timeline([{"video_key": "/a.mp4", "duration": 0.0}])
