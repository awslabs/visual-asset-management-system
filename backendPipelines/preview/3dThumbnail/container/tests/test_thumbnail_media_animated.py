#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Large animated GIF/WebP previews (#1550, owner decision D4).

An animated GIF or WebP at or over the size limit gets a smaller animated GIF when one fits after
reducing frames, then size, then palette; otherwise a first-frame JPEG. The ladder is exercised on small
files with a lowered limit (the limit is read at call time) and once end to end on files over the real
5 MiB limit."""

import os

import pytest
from PIL import Image

import media_samples as samples
from conftest import is_stubbed
from preview_pipeline import media_preview
from preview_pipeline.format_handlers import image_handler
from preview_pipeline.utils import image_utils

pytestmark = pytest.mark.skipif(
    is_stubbed("PIL") or is_stubbed("PIL.Image"), reason="needs the real Pillow")

LIMIT = 5 * 1024 * 1024
BUDGET = 200_000_000


def _gif_info(path):
    with Image.open(path) as gif:
        total = 0
        for index in range(gif.n_frames):
            gif.seek(index)
            total += gif.info.get("duration", 0)
        return gif.n_frames, total, gif.size


def _generate(path, output_dir, output_type="auto"):
    return media_preview.generate(str(path), os.path.splitext(path)[1], output_type, str(output_dir),
                                  os.path.basename(path), BUDGET)


def _frames(count, width=120, height=90, noisy=True):
    return samples.animated_frames(count, width, height, noisy=noisy)


@pytest.mark.unit
class TestLadderOrder:
    """Frames first, then size, then palette; it stops at the first rung under the limit."""

    def test_it_stops_at_the_first_rung_that_fits(self, tmp_path):
        frames = _frames(40, 100, 80)
        durations = [100] * 40
        out = str(tmp_path / "a.gif")
        full = image_utils.write_animated_gif(frames, durations, out) or os.path.getsize(out)
        limit = int(full * 0.4)
        result = image_utils.encode_animated_under_limit(frames, durations, out, max_bytes=limit)
        assert result == out and os.path.getsize(out) < limit
        count, total, size = _gif_info(out)
        assert count < 40
        assert size == (100, 80)  # frames were reduced; the size was not touched

    def test_the_loop_length_is_kept_when_frames_are_dropped(self, tmp_path):
        frames = _frames(40, 100, 80)
        out = str(tmp_path / "a.gif")
        image_utils.write_animated_gif(frames, [100] * 40, out)
        limit = int(os.path.getsize(out) * 0.4)
        image_utils.encode_animated_under_limit(frames, [100] * 40, out, max_bytes=limit)
        count, total, _ = _gif_info(out)
        assert count < 40 and total == pytest.approx(4000, abs=count * 10)

    def test_size_is_reduced_after_frames_are_exhausted(self, tmp_path):
        frames = _frames(8, 1000, 700)
        out = str(tmp_path / "a.gif")
        image_utils.write_animated_gif([image_utils.fit_long_edge(f, 800) for f in frames],
                                       [100] * 8, out)
        eight_at_800 = os.path.getsize(out)
        result = image_utils.encode_animated_under_limit(frames, [100] * 8, out,
                                                         max_bytes=int(eight_at_800 * 0.7))
        assert result == out
        count, _, size = _gif_info(out)
        assert count == 8 and max(size) < 800

    def test_the_palette_is_the_last_step(self, tmp_path):
        frames = _frames(8, 320, 240)
        out = str(tmp_path / "a.gif")
        image_utils.write_animated_gif(frames, [100] * 8, out, 256)
        at_256 = os.path.getsize(out)
        image_utils.write_animated_gif(frames, [100] * 8, out, 64)
        at_64 = os.path.getsize(out)
        assert at_64 < at_256
        # a limit between the two is only met by the palette step, with every frame and size kept
        ladder = image_utils.ANIMATED_LADDER
        result = image_utils.encode_animated_under_limit(frames, [100] * 8, out,
                                                         max_bytes=(at_256 + at_64) // 2 + 1,
                                                         ladder=ladder)
        assert result == out
        count, _, size = _gif_info(out)
        assert count == 8 and size == (320, 240)

    def test_it_returns_none_when_nothing_fits(self, tmp_path):
        frames = _frames(12, 200, 150)
        out = str(tmp_path / "a.gif")
        assert image_utils.encode_animated_under_limit(frames, [100] * 12, out, max_bytes=2000) is None
        assert not os.path.exists(out)

    def test_the_encode_cap_is_respected(self, tmp_path):
        frames = _frames(40, 100, 80)
        out = str(tmp_path / "a.gif")
        calls = []
        original = image_utils.write_animated_gif

        def counting(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)

        import unittest.mock as mock
        with mock.patch.object(image_utils, "write_animated_gif", counting):
            result = image_utils.encode_animated_under_limit(frames, [100] * 40, out, max_bytes=10,
                                                             max_encodes=3)
        assert result is None and len(calls) == 3

    def test_the_time_cap_is_respected(self, tmp_path):
        frames = _frames(40, 100, 80)
        out = str(tmp_path / "a.gif")
        ticks = iter([0.0, 0.0, 100.0, 200.0, 300.0, 400.0, 500.0, 600.0, 700.0, 800.0, 900.0, 1000.0])
        calls = []
        import unittest.mock as mock
        original = image_utils.write_animated_gif

        def counting(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)

        with mock.patch.object(image_utils, "write_animated_gif", counting):
            result = image_utils.encode_animated_under_limit(
                frames, [100] * 40, out, max_bytes=10, max_seconds=60.0, clock=lambda: next(ticks))
        assert result is None and len(calls) == 1

    def test_a_rung_that_repeats_an_earlier_encode_is_skipped(self, tmp_path):
        frames = _frames(6, 100, 80)  # fewer than every frame-count rung, smaller than every edge
        out = str(tmp_path / "a.gif")
        calls = []
        import unittest.mock as mock
        original = image_utils.write_animated_gif

        def counting(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)

        with mock.patch.object(image_utils, "write_animated_gif", counting):
            image_utils.encode_animated_under_limit(frames, [100] * 6, out, max_bytes=10)
        # (all, 256) then (6 of 8 frames -> same), edges all equal the frame's own: only the palettes differ
        assert len(calls) == 3

    def test_a_frame_delay_is_never_below_the_gif_minimum(self, tmp_path):
        out = str(tmp_path / "a.gif")
        image_utils.write_animated_gif(_frames(3, 20, 20, noisy=False), [1, 5, 40], out)
        with Image.open(out) as gif:
            delays = []
            for index in range(gif.n_frames):
                gif.seek(index)
                delays.append(gif.info["duration"])
        assert min(delays) >= image_utils.MIN_GIF_FRAME_MS

    def test_frame_sampling_is_even_and_keeps_the_first_frame(self):
        assert image_utils.sample_frames(100, 8)[0] == 0
        assert image_utils.sample_frames(100, 8)[-1] == 99
        assert len(image_utils.sample_frames(100, 8)) == 8
        assert image_utils.sample_frames(5, 12) == [0, 1, 2, 3, 4]
        assert image_utils.sample_frames(5, None) == [0, 1, 2, 3, 4]
        assert image_utils.sample_frames(5, 1) == [0]


@pytest.mark.unit
class TestAnimatedImagesEndToEnd:
    def test_a_large_animated_gif_fits_after_frame_reduction(self, tmp_path):
        # 24 frames of 800 x 450 noise: about 8.7 MiB; a smaller GIF fits with fewer frames
        path = samples.write_animated_gif(str(tmp_path / "big.gif"), count=24, width=800, height=450,
                                          noisy=True)
        assert os.path.getsize(path) > LIMIT
        result = _generate(path, tmp_path)
        assert result.path.endswith("big.gif.previewFile.gif")
        assert os.path.getsize(result.path) < LIMIT
        count, total, size = _gif_info(result.path)
        assert count < 24 and size == (800, 450)
        assert total == pytest.approx(2400, abs=count * 10)

    def test_a_large_animated_webp_takes_the_same_path(self, tmp_path):
        path = samples.write_animated_webp(str(tmp_path / "big.webp"), count=24, width=800, height=450,
                                           noisy=True)
        assert os.path.getsize(path) > LIMIT
        result = _generate(path, tmp_path)
        assert result.path.endswith("big.webp.previewFile.gif")
        assert os.path.getsize(result.path) < LIMIT
        assert _gif_info(result.path)[0] > 1

    def test_no_rung_fitting_writes_the_first_frame_as_a_jpeg(self, tmp_path, monkeypatch):
        path = samples.write_animated_gif(str(tmp_path / "big.gif"), count=12, width=300, height=200,
                                          noisy=True)
        monkeypatch.setattr(image_utils, "MAX_FILE_SIZE_BYTES", 150_000)
        assert os.path.getsize(path) > 150_000
        result = _generate(path, tmp_path)
        assert result.path.endswith("big.gif.previewFile.jpg")
        assert os.path.getsize(result.path) < 150_000

    def test_output_type_jpg_writes_the_first_frame_without_the_ladder(self, tmp_path, monkeypatch):
        path = samples.write_animated_gif(str(tmp_path / "big.gif"), count=12, width=300, height=200,
                                          noisy=True)
        monkeypatch.setattr(image_utils, "MAX_FILE_SIZE_BYTES", 150_000)
        result = _generate(path, tmp_path, output_type="jpg")
        assert result.path.endswith(".previewFile.jpg")

    def test_a_still_gif_over_the_limit_is_a_jpeg(self, tmp_path, monkeypatch):
        path = samples.write_animated_gif(str(tmp_path / "still.gif"), count=1, width=300, height=200,
                                          noisy=True)
        monkeypatch.setattr(image_utils, "MAX_FILE_SIZE_BYTES", os.path.getsize(path) - 1)
        result = _generate(path, tmp_path)
        assert result.path.endswith(".previewFile.jpg")

    def test_a_long_animation_is_sampled_to_at_most_the_frame_cap(self, tmp_path):
        path = samples.write_animated_gif(str(tmp_path / "long.gif"), count=350, width=16, height=12,
                                          duration=40)
        frames, durations = image_handler.load_animation(path, ".gif", BUDGET)
        assert len(frames) == image_handler.MAX_ANIMATION_FRAMES
        assert sum(durations) == pytest.approx(350 * 40, abs=len(frames) * 10)

    def test_an_animation_with_zero_delays_uses_the_default(self, tmp_path):
        path = samples.write_animated_gif(str(tmp_path / "z.gif"), count=3, duration=0)
        _, durations = image_handler.load_animation(path, ".gif", BUDGET)
        assert all(d >= image_utils.MIN_GIF_FRAME_MS for d in durations)

    def test_an_animation_over_the_pixel_budget_is_refused(self, tmp_path):
        path = samples.write_animated_gif(str(tmp_path / "px.gif"), count=3, width=200, height=200)
        with pytest.raises(image_handler.ImageTooLarge):
            image_handler.load_animation(path, ".gif", 1000)
