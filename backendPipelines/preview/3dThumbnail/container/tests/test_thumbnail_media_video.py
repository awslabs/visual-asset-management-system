#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Video previews (#1550): an animated GIF of frames sampled across the video, read with PyAV.

Videos are generated in the test with PyAV's own encoders (nothing binary is committed): a clip whose
red channel ramps from 0 to 255 over its length, so the sampled frames show whether they were taken
across the whole duration, with a green top half to tell a rotation."""

import os

import pytest
from PIL import Image

import media_samples as samples
from conftest import is_stubbed
from preview_pipeline import media_preview
from preview_pipeline.format_handlers import video_handler
from preview_pipeline.format_handlers.image_handler import MediaError
from preview_pipeline.utils import image_utils

av = pytest.importorskip("av")
pytestmark = pytest.mark.skipif(
    is_stubbed("PIL") or is_stubbed("PIL.Image"), reason="needs the real Pillow")

LIMIT = 5 * 1024 * 1024
BUDGET = 200_000_000

# (container format, encoder, extension)
VIDEOS = [
    ("mp4", "mpeg4", ".mp4"),
    ("mp4", "libx264", ".m4v"),
    ("mov", "mpeg4", ".mov"),
    ("matroska", "libvpx", ".mkv"),
    ("webm", "libvpx", ".webm"),
    ("avi", "mpeg4", ".avi"),
    ("asf", "wmv2", ".wmv"),
    ("mpeg", "mpeg2video", ".mpg"),
    ("mpeg", "mpeg2video", ".mpeg"),
    ("mpegts", "mpeg2video", ".mts"),
    ("mpegts", "mpeg2video", ".m2ts"),
]


def _generate(path, output_dir, output_type="auto"):
    return media_preview.generate(str(path), os.path.splitext(path)[1], output_type, str(output_dir),
                                  os.path.basename(path), BUDGET)


@pytest.mark.unit
class TestVideoFormats:
    @pytest.mark.parametrize("container,codec,ext", VIDEOS)
    def test_each_accepted_video_type_gives_a_gif_under_the_limit(self, tmp_path, container, codec, ext):
        path = samples.write_video(str(tmp_path / f"clip{ext}"), container, codec)
        result = _generate(path, tmp_path)
        assert result.path.endswith(f"clip{ext}.previewFile.gif")
        assert os.path.getsize(result.path) < LIMIT
        with Image.open(result.path) as gif:
            assert gif.n_frames > 1 and gif.format == "GIF"

    def test_the_accepted_extensions_are_the_issues_list(self):
        assert video_handler.SUPPORTED_EXTENSIONS == {
            ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".mpg", ".mpeg", ".mts", ".m2ts"}


@pytest.mark.unit
class TestSampling:
    def test_frames_are_spread_across_the_whole_duration(self, tmp_path):
        path = samples.write_video(str(tmp_path / "ramp.mp4"), "mp4", "mpeg4", frames=60, fps=10)
        frames, duration = video_handler.sample_frames(path, ".mp4")
        assert len(frames) == video_handler.DEFAULT_SAMPLE_FRAMES == 12
        assert duration == pytest.approx(6.0, abs=0.2)
        reds = [frame.getpixel((frame.width // 2, frame.height - 5))[0] for frame in frames]
        assert reds == sorted(reds)
        assert reds[0] < 60 and reds[-1] > 200

    def test_the_sample_count_is_capped(self, tmp_path):
        path = samples.write_video(str(tmp_path / "ramp.mp4"), "mp4", "mpeg4", frames=60, fps=10)
        frames, _ = video_handler.sample_frames(path, ".mp4", count=500)
        assert len(frames) == video_handler.MAX_SAMPLE_FRAMES

    def test_the_long_edge_is_at_most_480(self, tmp_path):
        path = samples.write_video(str(tmp_path / "wide.mp4"), "mp4", "mpeg4", width=1280, height=720,
                                   frames=10)
        frames, _ = video_handler.sample_frames(path, ".mp4")
        assert max(frames[0].size) == 480

    def test_a_small_video_is_not_scaled_up(self, tmp_path):
        path = samples.write_video(str(tmp_path / "small.mp4"), "mp4", "mpeg4", width=64, height=48,
                                   frames=10)
        frames, _ = video_handler.sample_frames(path, ".mp4")
        assert frames[0].size == (64, 48)

    def test_the_gif_plays_at_about_four_frames_a_second(self, tmp_path):
        path = samples.write_video(str(tmp_path / "clip.mp4"), "mp4", "mpeg4")
        result = _generate(path, tmp_path)
        with Image.open(result.path) as gif:
            assert gif.info["duration"] == video_handler.GIF_FRAME_MS == 250

    def test_rotation_metadata_is_applied(self, tmp_path):
        path = samples.write_video(str(tmp_path / "turned.mp4"), "mp4", "mpeg4", width=160, height=90,
                                   frames=10, rotation=90)
        frames, _ = video_handler.sample_frames(path, ".mp4")
        # a 160 x 90 video displayed with a 90 degree rotation is 90 x 160
        assert frames[0].size == (90, 160)

    def test_no_rotation_leaves_the_frame_as_encoded(self, tmp_path):
        path = samples.write_video(str(tmp_path / "plain.mp4"), "mp4", "mpeg4", width=160, height=90,
                                   frames=10)
        frames, _ = video_handler.sample_frames(path, ".mp4")
        assert frames[0].size == (160, 90)

    def test_an_unreadable_duration_falls_back_to_reading_from_the_start(self, tmp_path, monkeypatch):
        path = samples.write_video(str(tmp_path / "clip.mp4"), "mp4", "mpeg4", frames=30)
        monkeypatch.setattr(video_handler, "_duration_seconds", lambda *a, **k: None)
        frames, duration = video_handler.sample_frames(path, ".mp4")
        assert duration is None and 1 < len(frames) <= 12

    def test_the_decode_time_limit_keeps_the_frames_taken_so_far(self, tmp_path):
        path = samples.write_video(str(tmp_path / "clip.mp4"), "mp4", "mpeg4", frames=60, fps=10)
        ticks = iter([0.0, 0.0, 0.0, 1000.0] + [1000.0] * 200)
        frames, _ = video_handler.sample_frames(path, ".mp4", time_limit=600.0,
                                                clock=lambda: next(ticks))
        assert 1 <= len(frames) < 12

    def test_a_video_that_will_not_fit_as_a_gif_gives_the_middle_frame_as_a_jpeg(self, tmp_path,
                                                                                monkeypatch):
        path = samples.write_video(str(tmp_path / "clip.mp4"), "mp4", "mpeg4", width=320, height=240)
        monkeypatch.setattr(image_utils, "MAX_FILE_SIZE_BYTES", 3000)
        result = _generate(path, tmp_path)
        assert result.path.endswith(".previewFile.jpg") and os.path.getsize(result.path) < 3000

    @pytest.mark.parametrize("output_type,extension", [("jpg", "jpg"), ("png", "png")])
    def test_output_type_jpg_or_png_writes_the_middle_frame(self, tmp_path, output_type, extension):
        path = samples.write_video(str(tmp_path / "clip.mp4"), "mp4", "mpeg4")
        result = _generate(path, tmp_path, output_type=output_type)
        assert result.path.endswith(f".previewFile.{extension}")
        with Image.open(result.path) as image:
            assert image.format == extension.upper().replace("JPG", "JPEG")


@pytest.mark.unit
class TestUnreadableVideo:
    def test_an_audio_only_file_completes_without_a_preview(self, tmp_path):
        path = samples.write_audio_only(str(tmp_path / "audio.mkv"))
        result = _generate(path, tmp_path)
        assert result.path is None
        assert result.message == "No video stream in 'audio.mkv'; no preview written."

    def test_a_zero_length_video_fails_with_a_message_naming_the_type(self, tmp_path):
        (tmp_path / "zero.mp4").write_bytes(b"")
        with pytest.raises(MediaError) as caught:
            _generate(str(tmp_path / "zero.mp4"), tmp_path)
        assert "MP4" in str(caught.value)

    def test_a_truncated_video_fails_or_previews_what_it_has(self, tmp_path):
        good = samples.write_video(str(tmp_path / "good.mp4"), "mp4", "mpeg4", frames=60)
        data = open(good, "rb").read()
        (tmp_path / "cut.mp4").write_bytes(data[: len(data) // 3])
        try:
            result = _generate(str(tmp_path / "cut.mp4"), tmp_path)
        except MediaError as e:
            assert "MP4" in str(e)
        else:
            # a moov atom at the end is gone, or the file still plays up to the cut
            assert result.path is None or os.path.getsize(result.path) < LIMIT

    def test_garbage_with_a_video_extension_fails_with_a_message(self, tmp_path):
        (tmp_path / "junk.avi").write_bytes(os.urandom(4096))
        with pytest.raises(MediaError) as caught:
            _generate(str(tmp_path / "junk.avi"), tmp_path)
        assert "AVI" in str(caught.value) and "Traceback" not in str(caught.value)

    def test_a_video_with_an_unsupported_codec_fails_with_a_message(self, tmp_path, monkeypatch):
        path = samples.write_video(str(tmp_path / "clip.mp4"), "mp4", "mpeg4")

        class _Broken:
            def decode(self, *a, **k):
                raise av.error.InvalidDataError(1, "unsupported codec")

        original = video_handler._seek_sample

        def broken(container, stream, *a, **k):
            container.decode = _Broken().decode
            return original(container, stream, *a, **k)

        monkeypatch.setattr(video_handler, "_seek_sample", broken)
        monkeypatch.setattr(video_handler, "_sequential_sample",
                            lambda *a, **k: (_ for _ in ()).throw(av.error.InvalidDataError(1, "codec")))
        with pytest.raises(MediaError) as caught:
            _generate(path, tmp_path)
        assert "MP4" in str(caught.value)


@pytest.mark.unit
class TestOpeningIsRestricted:
    def test_a_file_is_opened_as_a_local_file_only(self):
        assert video_handler.CONTAINER_OPTIONS == {"protocol_whitelist": "file"}

    def test_only_allowlisted_demuxers_are_tried(self, tmp_path, monkeypatch):
        opened = []
        real_open = av.open

        def spy(path, mode="r", format=None, options=None, **kwargs):
            opened.append((format, options))
            return real_open(path, mode=mode, format=format, options=options, **kwargs)

        monkeypatch.setattr(av, "open", spy)
        path = samples.write_video(str(tmp_path / "clip.mkv"), "matroska", "libvpx")
        opened.clear()
        video_handler.sample_frames(path, ".mkv")
        assert opened and all(fmt in video_handler.ALLOWED_FORMATS for fmt, _ in opened)
        assert all(options == {"protocol_whitelist": "file"} for _, options in opened)

    def test_a_playlist_that_names_a_network_url_is_never_fetched(self, tmp_path):
        playlist = tmp_path / "list.mp4"
        playlist.write_text("ffconcat version 1.0\nfile 'http://127.0.0.1:9/x.mp4'\n")
        with pytest.raises(MediaError):
            _generate(str(playlist), tmp_path)

    def test_a_video_whose_content_is_another_allowed_container_still_opens(self, tmp_path):
        path = samples.write_video(str(tmp_path / "actually.mp4"), "matroska", "libvpx")
        os.replace(path, tmp_path / "mislabelled.mpg")
        frames, _ = video_handler.sample_frames(str(tmp_path / "mislabelled.mpg"), ".mpg")
        assert frames

    def test_the_image_has_no_debian_ffmpeg(self):
        """PyAV's wheels carry the FFmpeg libraries; the Dockerfile installs none of its own."""
        dockerfile = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Dockerfile")
        with open(dockerfile) as handle:
            text = "\n".join(line for line in handle.read().splitlines() if not line.lstrip().startswith("#"))
        assert "ffmpeg" not in text.lower() and "libav" not in text.lower()
