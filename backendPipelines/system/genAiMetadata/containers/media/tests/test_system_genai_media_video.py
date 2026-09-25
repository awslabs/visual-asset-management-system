#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Video: stream facts are parsed from ffmpeg's own header (the imageio-ffmpeg wheel ships no ffprobe) under
the registry's key names (videoCodec, frameRate, sampleRate, channels, tags), cross-filled from imageio-ffmpeg
metadata, and four evenly spaced keyframes become vision-ready PNGs. One failing keyframe is a warning; none
decoding is renderSkipped "error"; no facts at all is an error."""

import os

import pytest
from PIL import Image

from media_extractors import video
from media_extractors.common import RENDER_SKIPPED_ERROR
from system_genai_media_fixtures import (
    FFMPEG_HEADER_SAMPLE, FFMPEG_HEADER_WITH_TAGS, FakeFfmpeg, failing_read_frames, fake_read_frames, make_ctx,
    write,
)


@pytest.fixture(autouse=True)
def _fake_ffmpeg_exe(monkeypatch):
    # imageio_ffmpeg.get_ffmpeg_exe() returns this without probing a binary, so no ffmpeg runs in the tests.
    monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", "ffmpeg-under-test")


@pytest.mark.unit
class TestParseHeader:
    def test_stream_facts(self):
        facts = video.parse_ffmpeg_header_text(FFMPEG_HEADER_SAMPLE)
        assert facts["container"].startswith("mov,mp4")
        assert facts["durationSeconds"] == 92.48
        assert facts["bitrateKbps"] == 4628
        assert (facts["videoCodec"], facts["pixelFormat"]) == ("h264", "yuv420p")
        assert (facts["width"], facts["height"]) == (1920, 1080)
        assert facts["frameRate"] == 29.97
        assert facts["videoBitrateKbps"] == 4500
        assert (facts["streamCount"], facts["videoStreams"], facts["audioStreams"]) == (2, 1, 1)
        assert facts["audioCodec"] == "aac"
        assert facts["sampleRate"] == 48000
        assert facts["channelLayout"] == "stereo" and facts["channels"] == 2
        assert facts["audioBitrateKbps"] == 128
        assert facts["rotation"] == 270
        assert "tags" not in facts
        # The pre-rename spellings are gone: the promotion catalogue reads the registry names only.
        assert not {"codec", "fps", "audioSampleRateHz", "audioChannels"} & set(facts)

    def test_output_section_is_ignored(self):
        # The null muxer's output streams (wrapped_avframe / pcm_s16le) must not be counted as input streams.
        facts = video.parse_ffmpeg_header_text(FFMPEG_HEADER_SAMPLE)
        assert facts["videoCodec"] != "wrapped_avframe" and facts["audioCodec"] != "pcm_s16le"

    def test_format_tags_become_sys_media_tags(self):
        facts = video.parse_ffmpeg_header_text(FFMPEG_HEADER_WITH_TAGS)
        assert facts["tags"] == {"title": "Proto Clip", "artist": "Proto Artist", "album": "Proto Album", "year": 2026}
        # Stream-level metadata (handler_name, vendor_id) is not a tag.
        assert video.parse_format_tags(FFMPEG_HEADER_WITH_TAGS) == facts["tags"]
        assert "handler_name" not in facts["tags"]

    def test_channel_layouts_map_to_counts(self):
        assert video.parse_channel_count("mono") == 1
        assert video.parse_channel_count("stereo") == 2
        assert video.parse_channel_count("5.1(side)") == 6
        assert video.parse_channel_count("7.1(wide)") == 8
        assert video.parse_channel_count("2 channels") == 2
        assert video.parse_channel_count("hexadecagonal") is None
        assert video.parse_channel_count(None) is None

    def test_rotate_metadata_form(self):
        header = FFMPEG_HEADER_SAMPLE.replace("displaymatrix: rotation of -90.00 degrees", "rotate           : 90")
        assert video.parse_ffmpeg_header_text(header)["rotation"] == 90

    def test_no_rotation(self):
        header = FFMPEG_HEADER_SAMPLE.replace("        displaymatrix: rotation of -90.00 degrees\n", "")
        assert video.parse_ffmpeg_header_text(header)["rotation"] == 0

    def test_empty_text(self):
        assert video.parse_ffmpeg_header_text("") == {
            "streamCount": 0, "videoStreams": 0, "audioStreams": 0, "rotation": 0}


@pytest.mark.unit
def test_keyframe_times_are_segment_midpoints():
    assert video.keyframe_times(92.48) == [11.56, 34.68, 57.8, 80.92]
    assert video.keyframe_times(0) == [0.0]
    assert video.keyframe_times(10, count=2) == [2.5, 7.5]


@pytest.mark.unit
class TestProbeVideo:
    def test_both_sources_agree(self, tmp_path):
        path = write(tmp_path, "clip.mp4", b"\x00")
        facts, warnings = video.probe_video(path, run=FakeFfmpeg(), read_frames=fake_read_frames)
        assert warnings == []
        assert facts["durationSeconds"] == 92.48 and facts["videoCodec"] == "h264"

    def test_probe_argv_is_the_null_muxer_bounded_to_one_frame_per_stream(self, tmp_path):
        path = write(tmp_path, "clip.mp4", b"\x00")
        fake = FakeFfmpeg()
        video.probe_video(path, run=fake, read_frames=fake_read_frames)
        argv = fake.calls[0]
        assert argv[0] == "ffmpeg-under-test"
        assert argv[1:3] == ["-hide_banner", "-nostdin"]
        assert argv[-2:] == ["null", "-"] and argv[-3] == "-f"
        assert argv[argv.index("-frames:v") + 1] == "1" and argv[argv.index("-frames:a") + 1] == "1"

    def test_read_frames_failure_is_a_warning(self, tmp_path):
        path = write(tmp_path, "clip.mp4", b"\x00")
        facts, warnings = video.probe_video(path, run=FakeFfmpeg(), read_frames=failing_read_frames)
        assert facts["width"] == 1920
        assert len(warnings) == 1 and "imageio-ffmpeg" in warnings[0]

    def test_header_failure_falls_back_to_read_frames(self, tmp_path):
        path = write(tmp_path, "clip.mp4", b"\x00")

        def broken_run(argv, **kwargs):
            raise OSError("ffmpeg missing")

        facts, warnings = video.probe_video(path, run=broken_run, read_frames=fake_read_frames)
        assert (facts["width"], facts["height"], facts["frameRate"]) == (1920, 1080, 29.97)
        assert facts["durationSeconds"] == 92.48 and facts["audioCodec"] == "aac"
        assert facts["videoCodec"] == "h264" and "codec" not in facts
        assert len(warnings) == 1 and "header probe failed" in warnings[0]

    def test_both_failing_is_an_error(self, tmp_path):
        path = write(tmp_path, "clip.mp4", b"\x00")
        with pytest.raises(RuntimeError, match="No video facts"):
            video.probe_video(path, run=FakeFfmpeg(header="", probe_returncode=1), read_frames=failing_read_frames)


@pytest.mark.unit
class TestExtractVideo:
    def test_four_keyframes_and_sys_media(self, tmp_path):
        path = write(tmp_path, "clip.mp4", b"\x00")
        fake = FakeFfmpeg()
        result = video.extract_video(path, make_ctx(tmp_path, "clip.mp4"), run=fake, read_frames=fake_read_frames)
        sys_media = result.attributes["sys_media"]
        assert result.file_class == "video" and sys_media["kind"] == "video"
        assert (sys_media["width"], sys_media["height"]) == (1920, 1080)
        assert (sys_media["displayWidth"], sys_media["displayHeight"]) == (1080, 1920)
        assert sys_media["rotation"] == 270 and sys_media["audioCodec"] == "aac"
        assert sys_media["videoCodec"] == "h264" and sys_media["frameRate"] == 29.97
        assert sys_media["sampleRate"] == 48000 and sys_media["channels"] == 2
        assert result.facts["duration"] == "1 min 32 s"
        assert result.facts["resolution"] == "1080 x 1920 px"
        assert result.facts["frameRate"] == "29.97 fps"
        assert result.facts["videoCodec"] == "h264" and result.facts["audioCodec"] == "aac"
        assert [os.path.basename(p) for p in result.render_images] == [
            "media-01.png", "media-02.png", "media-03.png", "media-04.png"]
        for rendered in result.render_images:
            with Image.open(rendered) as image:
                assert image.format == "PNG" and image.size == (64, 36)
        assert result.render_skipped is None and result.warnings == []
        seeks = [call[call.index("-ss") + 1] for call in fake.calls if "-ss" in call]
        assert seeks == ["11.560", "34.680", "57.800", "80.920"]
        keyframe_call = [call for call in fake.calls if "-ss" in call][0]
        assert keyframe_call.index("-ss") < keyframe_call.index("-i")
        assert "-frames:v" in keyframe_call and keyframe_call[keyframe_call.index("-vf") + 1] == "scale='min(1568,iw)':-2"

    def test_one_failed_keyframe_is_a_warning(self, tmp_path):
        path = write(tmp_path, "clip.mp4", b"\x00")
        result = video.extract_video(
            path, make_ctx(tmp_path, "clip.mp4"), run=FakeFfmpeg(fail_at=(34.68,)), read_frames=fake_read_frames)
        assert len(result.render_images) == 3
        assert [os.path.basename(p) for p in result.render_images] == ["media-01.png", "media-03.png", "media-04.png"]
        assert len(result.warnings) == 1 and "34.7s" in result.warnings[0]
        assert result.render_skipped is None

    def test_no_keyframe_is_an_error_skip_with_attributes_kept(self, tmp_path):
        path = write(tmp_path, "clip.mp4", b"\x00")
        result = video.extract_video(
            path, make_ctx(tmp_path, "clip.mp4"),
            run=FakeFfmpeg(fail_at=(11.56, 34.68, 57.8, 80.92)), read_frames=fake_read_frames)
        assert result.render_images == []
        assert result.render_skipped == RENDER_SKIPPED_ERROR
        assert result.attributes["sys_media"]["durationSeconds"] == 92.48
        assert len(result.warnings) == 4

    def test_undecodable_video_raises(self, tmp_path):
        path = write(tmp_path, "clip.mp4", b"\x00")
        with pytest.raises(RuntimeError):
            video.extract_video(
                path, make_ctx(tmp_path, "clip.mp4"),
                run=FakeFfmpeg(header="", probe_returncode=1), read_frames=failing_read_frames)
