#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Raster images: dimensions, mode and the EXIF subset land in sys_image, exactly one normalised PNG is
produced, oversized or undecodable images degrade to attributes-only with the matching renderSkipped, and
EXIF GPS is decoded into decimal degrees only when the state's extractGeoLocation flag says so (hasGps is
reported either way)."""

import io
import os

import pytest
from PIL import Image

from media_extractors import images
from media_extractors.common import RENDER_SKIPPED_ERROR, RENDER_SKIPPED_SIZE
from system_genai_media_fixtures import (
    SEATTLE_GPS, SYDNEY_GPS, gif_bytes, jpeg_with_exif_bytes, make_ctx, png_bytes, write,
)


@pytest.mark.unit
class TestExtractImage:
    def test_png_dimensions_mode_and_one_render(self, tmp_path):
        path = write(tmp_path, "photo.png", png_bytes(3000, 1000))
        ctx = make_ctx(tmp_path, "photo.png", content_type="image/png")
        result = images.extract_image(path, ctx)
        sys_image = result.attributes["sys_image"]
        assert result.file_class == "image"
        assert sys_image["format"] == "PNG"
        assert (sys_image["width"], sys_image["height"], sys_image["mode"]) == (3000, 1000, "RGB")
        assert sys_image["megapixels"] == 3.0
        assert sys_image["frames"] == 1 and sys_image["animated"] is False and sys_image["hasAlpha"] is False
        assert "exif" not in sys_image
        assert result.facts["dimensions"] == "3000 x 1000 px"
        assert result.facts["imageFormat"] == "PNG"
        assert result.facts["analysisImage"] == "1568 x 523 px PNG"
        assert result.render_skipped is None
        assert len(result.render_images) == 1
        with Image.open(result.render_images[0]) as rendered:
            assert rendered.format == "PNG" and rendered.size == (1568, 523)
        assert os.path.dirname(result.render_images[0]) == ctx.work_dir

    def test_jpeg_exif_subset_and_camera_facts_without_geo_location(self, tmp_path):
        # extractGeoLocation false: the GPS block is acknowledged (hasGps) and no coordinate is written.
        path = write(tmp_path, "shot.jpg", jpeg_with_exif_bytes(gps=SYDNEY_GPS))
        result = images.extract_image(path, make_ctx(tmp_path, "shot.jpg", extract_geo_location=False))
        exif = result.attributes["sys_image"]["exif"]
        assert exif["make"] == "ProtoMake" and exif["model"] == "ProtoModel"
        assert exif["orientation"] == 6
        assert exif["dateTimeOriginal"] == "2026:01:02 03:04:05"
        assert exif["hasGps"] is True
        assert "gps" not in exif
        assert result.facts["camera"] == "ProtoMake ProtoModel"
        assert result.facts["captured"] == "2026:01:02 03:04:05"
        assert "location" not in result.facts and result.warnings == []

    def test_gps_is_decoded_when_the_state_enables_it(self, tmp_path):
        # Seattle: northern and western hemispheres, altitude above sea level.
        path = write(tmp_path, "shot.jpg", jpeg_with_exif_bytes(gps=SEATTLE_GPS))
        result = images.extract_image(path, make_ctx(tmp_path, "shot.jpg", extract_geo_location=True))
        exif = result.attributes["sys_image"]["exif"]
        assert exif["hasGps"] is True
        assert exif["gps"] == pytest.approx({"latitude": 47.609722, "longitude": -122.333056, "altitude": 56.0}, abs=1e-6)
        assert result.facts["location"] == "47.609722, -122.333056"
        assert result.warnings == []

    def test_southern_and_eastern_hemispheres(self, tmp_path):
        path = write(tmp_path, "shot.jpg", jpeg_with_exif_bytes(gps=SYDNEY_GPS))
        gps = images.extract_image(path, make_ctx(tmp_path, "shot.jpg", extract_geo_location=True)).attributes["sys_image"]["exif"]["gps"]
        assert gps == pytest.approx({"latitude": -33.865083, "longitude": 151.209944, "altitude": 12.5}, abs=1e-6)

    def test_missing_altitude_is_null(self, tmp_path):
        no_altitude = {key: value for key, value in SYDNEY_GPS.items() if key not in ("alt", "alt_ref")}
        path = write(tmp_path, "shot.jpg", jpeg_with_exif_bytes(gps=no_altitude))
        gps = images.extract_image(path, make_ctx(tmp_path, "shot.jpg", extract_geo_location=True)).attributes["sys_image"]["exif"]["gps"]
        assert set(gps) == {"latitude", "longitude", "altitude"}
        assert gps["altitude"] is None
        assert gps["latitude"] == pytest.approx(-33.865083, abs=1e-6)

    def test_gps_block_without_coordinates_is_a_warning_under_the_flag(self, tmp_path):
        # The default fixture writes only a hemisphere reference: the block exists, nothing decodes.
        path = write(tmp_path, "shot.jpg", jpeg_with_exif_bytes())
        result = images.extract_image(path, make_ctx(tmp_path, "shot.jpg", extract_geo_location=True))
        exif = result.attributes["sys_image"]["exif"]
        assert exif["hasGps"] is True and "gps" not in exif
        assert len(result.warnings) == 1 and "GPS" in result.warnings[0] and "decode" in result.warnings[0]
        assert "location" not in result.facts

    def test_animated_gif_reports_frames_and_renders_the_first(self, tmp_path):
        path = write(tmp_path, "anim.gif", gif_bytes(frames=3))
        result = images.extract_image(path, make_ctx(tmp_path, "anim.gif"))
        sys_image = result.attributes["sys_image"]
        assert sys_image["frames"] == 3 and sys_image["animated"] is True
        assert result.facts["frames"] == "3 frames"
        assert len(result.render_images) == 1
        with Image.open(result.render_images[0]) as rendered:
            assert rendered.size == (20, 20)

    def test_decompression_bomb_is_a_size_skip_without_decoding(self, tmp_path, monkeypatch):
        # Pillow raises DecompressionBombError at open() above twice MAX_IMAGE_PIXELS.
        monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
        path = write(tmp_path, "huge.png", png_bytes(20, 20))
        result = images.extract_image(path, make_ctx(tmp_path, "huge.png"))
        assert result.render_skipped == RENDER_SKIPPED_SIZE
        assert result.attributes["sys_image"] == {"format": "PNG", "decodable": False}
        assert result.render_images == []
        assert "pixel budget" in result.warnings[0]

    def test_raster_budget_keeps_header_attributes_but_skips_the_render(self, tmp_path, monkeypatch):
        monkeypatch.setattr(images, "MAX_RASTER_PIXELS", 1000)
        path = write(tmp_path, "big.png", png_bytes(40, 40))
        result = images.extract_image(path, make_ctx(tmp_path, "big.png"))
        assert result.render_skipped == RENDER_SKIPPED_SIZE
        assert result.attributes["sys_image"]["width"] == 40
        assert result.render_images == []
        assert "raster budget" in result.warnings[0]

    def test_undecodable_bytes_are_an_error_skip(self, tmp_path):
        path = write(tmp_path, "broken.png", b"not an image at all")
        result = images.extract_image(path, make_ctx(tmp_path, "broken.png"))
        assert result.render_skipped == RENDER_SKIPPED_ERROR
        assert result.attributes["sys_image"] == {"format": "PNG", "decodable": False}
        assert "could not be decoded" in result.warnings[0]


@pytest.mark.unit
class TestExifSubset:
    def test_image_without_exif_is_empty(self):
        assert images.exif_subset(Image.open(io.BytesIO(png_bytes(4, 4)))) == {}

    def test_rationals_become_floats_and_gps_absence_is_reported(self):
        exif = Image.Exif()
        exif[271] = "Maker"
        exif.get_ifd(0x8769)[33437] = 2.8
        buffer = io.BytesIO()
        Image.new("RGB", (4, 4)).save(buffer, "JPEG", exif=exif.tobytes())
        subset = images.exif_subset(Image.open(io.BytesIO(buffer.getvalue())), extract_geo_location=True)
        assert subset["make"] == "Maker"
        assert subset["fNumber"] == pytest.approx(2.8)
        assert subset["hasGps"] is False and "gps" not in subset

    def test_plain_values(self):
        assert images._plain(b"Bytes\x00") == "Bytes"
        assert images._plain(6) == 6
        assert images._plain("x" * 300) == "x" * 200


@pytest.mark.unit
class TestGpsDecimal:
    """gps_decimal over IFD dicts as Pillow returns them (tag id -> value)."""

    def test_below_sea_level_altitude_is_negative(self):
        from PIL.TiffImagePlugin import IFDRational

        ifd = {1: "N", 2: (IFDRational(31, 1), IFDRational(30, 1), IFDRational(0, 1)),
               3: "E", 4: (IFDRational(35, 1), IFDRational(30, 1), IFDRational(0, 1)),
               5: b"\x01", 6: IFDRational(430, 1)}
        assert images.gps_decimal(ifd) == {"latitude": 31.5, "longitude": 35.5, "altitude": -430.0}

    def test_missing_reference_reads_as_north_and_east(self):
        assert images.gps_decimal({2: (10.0, 30.0, 0.0), 4: (20.0, 0.0, 0.0)}) == {
            "latitude": 10.5, "longitude": 20.0, "altitude": None}

    def test_out_of_range_coordinates_are_rejected(self):
        assert images.gps_decimal({1: "N", 2: (95.0, 0.0, 0.0), 3: "E", 4: (20.0, 0.0, 0.0)}) is None
        assert images.gps_decimal({1: "N", 2: (10.0, 0.0, 0.0), 3: "E", 4: (181.0, 0.0, 0.0)}) is None

    def test_zero_denominator_and_partial_blocks_are_rejected(self):
        from PIL.TiffImagePlugin import IFDRational

        nan_ifd = {1: "N", 2: (IFDRational(1, 0), IFDRational(0, 1), IFDRational(0, 1)), 3: "E", 4: (20.0, 0.0, 0.0)}
        assert images.gps_decimal(nan_ifd) is None
        assert images.gps_decimal({1: "N"}) is None
        assert images.gps_decimal({1: "N", 2: (10.0, 0.0), 3: "E", 4: (20.0, 0.0, 0.0)}) is None
        assert images.gps_decimal(None) is None
