#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Every image handed to the vision model is a PNG within the Bedrock bounds: long edge <= 1568 px, encoded
size <= 3.75 MB, 8-bit RGB or RGBA. The byte cap is met by downscaling and the loop terminates."""

import io
import os

import pytest
from PIL import Image

from media_extractors import imaging
from media_extractors.common import VISION_MAX_BYTES, VISION_MAX_LONG_EDGE_PX
from system_genai_media_fixtures import noise_png_bytes, png_bytes

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _open(data):
    return Image.open(io.BytesIO(data))


@pytest.mark.unit
class TestNormaliseForVision:
    def test_wide_image_is_fitted_to_the_long_edge(self):
        data, size = imaging.normalise_for_vision(_open(png_bytes(3000, 1000)))
        assert data.startswith(_PNG_SIGNATURE)
        assert size == (VISION_MAX_LONG_EDGE_PX, 523)
        assert _open(data).size == size

    def test_tall_image_is_fitted_on_its_height(self):
        _, size = imaging.normalise_for_vision(_open(png_bytes(500, 4000)))
        assert size == (196, VISION_MAX_LONG_EDGE_PX)

    def test_small_image_keeps_its_size(self):
        _, size = imaging.normalise_for_vision(_open(png_bytes(640, 480)))
        assert size == (640, 480)

    def test_greyscale_becomes_rgb(self):
        data, _ = imaging.normalise_for_vision(_open(png_bytes(10, 10, mode="L")))
        assert _open(data).mode == "RGB"

    def test_palette_with_transparency_becomes_rgba(self):
        palette = Image.new("P", (10, 10), 1)
        palette.info["transparency"] = 1
        data, _ = imaging.normalise_for_vision(palette)
        assert _open(data).mode == "RGBA"

    def test_rgba_is_preserved(self):
        data, _ = imaging.normalise_for_vision(Image.new("RGBA", (10, 10), (1, 2, 3, 128)))
        assert _open(data).mode == "RGBA"

    def test_byte_cap_is_met_by_downscaling(self):
        data, size = imaging.normalise_for_vision(_open(noise_png_bytes(400, 400)), max_bytes=100_000)
        assert len(data) <= 100_000
        assert max(size) < 400
        assert _open(data).size == size

    def test_loop_terminates_at_the_minimum_edge_when_the_cap_is_unreachable(self):
        data, size = imaging.normalise_for_vision(_open(noise_png_bytes(300, 300)), max_bytes=10)
        assert max(size) <= 64
        assert data.startswith(_PNG_SIGNATURE)

    def test_defaults_are_the_spec_bounds(self):
        data, size = imaging.normalise_for_vision(_open(png_bytes(2000, 2000)))
        assert max(size) == VISION_MAX_LONG_EDGE_PX
        assert len(data) <= VISION_MAX_BYTES


@pytest.mark.unit
def test_write_png_writes_the_bytes_and_returns_the_path(tmp_path):
    path = imaging.write_png(b"abc", str(tmp_path), "media-01.png")
    assert path == os.path.join(str(tmp_path), "media-01.png")
    with open(path, "rb") as handle:
        assert handle.read() == b"abc"
