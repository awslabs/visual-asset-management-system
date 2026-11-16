#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Media previews of still images (#1550): what the run writes for each image type and size.

The decision table is in ``preview_pipeline.media_preview``. Samples are generated in the test
(``media_samples``): nothing binary is committed. Every preview written must be strictly under the
5 MiB limit, EXIF orientation applied, transparency on white."""

import os

import numpy as np
import pytest
from PIL import Image

import media_samples as samples
from conftest import is_stubbed
from preview_pipeline import media_preview
from preview_pipeline.format_handlers import image_handler
from preview_pipeline.format_handlers.image_handler import ImageTooLarge, MediaError
from preview_pipeline.utils import image_utils

pytestmark = pytest.mark.skipif(
    is_stubbed("PIL") or is_stubbed("PIL.Image"), reason="needs the real Pillow")

LIMIT = 5 * 1024 * 1024
BUDGET = 200_000_000


def _generate(path, output_dir, output_type="auto", downscale=False, budget=BUDGET):
    ext = os.path.splitext(path)[1]
    return media_preview.generate(str(path), ext, output_type, str(output_dir), os.path.basename(path),
                                  budget, downscale_self_previewable=downscale)


def _center(path):
    with Image.open(path) as img:
        img = img.convert("RGB")
        return img.size, img.getpixel((img.width // 2, img.height // 2))


@pytest.mark.unit
class TestSelfPreviewableImages:
    """An image the web shows as its own preview gets no generated preview by default."""

    @pytest.mark.parametrize("name,writer", [
        ("a.png", samples.write_png), ("a.jpg", samples.write_jpeg), ("a.jpeg", samples.write_jpeg),
        ("a.gif", samples.write_animated_gif), ("a.bmp", samples.write_bmp),
        ("a.webp", samples.write_animated_webp),
    ])
    def test_a_small_displayable_image_writes_nothing(self, tmp_path, name, writer):
        path = writer(str(tmp_path / name))
        result = _generate(path, tmp_path)
        assert result.path is None
        assert "is shown as its own preview" in result.message and name in result.message
        assert not [p for p in os.listdir(tmp_path) if ".previewFile." in p]

    def test_the_boundary_is_strictly_under_five_mib(self, tmp_path):
        under = samples.write_png_of_size(str(tmp_path / "under.png"), LIMIT - 1)
        exactly = samples.write_png_of_size(str(tmp_path / "exactly.png"), LIMIT)
        assert _generate(under, tmp_path).path is None
        result = _generate(exactly, tmp_path)
        assert result.path is not None and result.path.endswith("exactly.png.previewFile.jpg")
        assert os.path.getsize(result.path) < LIMIT

    def test_a_zero_byte_image_is_not_decoded_when_self_previewable(self, tmp_path):
        """Size 0 is under the limit and the extension displayable: the web treats it as its own
        preview, so the run writes nothing (the file is then simply broken in the viewer)."""
        path = tmp_path / "empty.png"
        path.write_bytes(b"")
        assert _generate(str(path), tmp_path).path is None

    def test_tga_and_tiff_are_never_self_previewable(self, tmp_path):
        for name, writer in (("a.tga", samples.write_tga), ("a.tif", samples.write_tiff_pillow),
                             ("a.tiff", samples.write_tiff_pillow)):
            path = writer(str(tmp_path / name))
            assert os.path.getsize(path) < LIMIT
            result = _generate(path, tmp_path)
            assert result.path and result.path.endswith(".previewFile.jpg"), name

    def test_unknown_size_is_not_self_previewable(self):
        assert image_handler.is_self_previewable(".png", None) is False
        assert image_handler.is_self_previewable(".png", LIMIT - 1) is True
        assert image_handler.is_self_previewable(".png", LIMIT) is False
        assert image_handler.is_self_previewable(".tif", 10) is False


@pytest.mark.unit
class TestDownscaleOption:
    """mediaPreviews.downscaleSelfPreviewableImages: a JPEG for an image the web already shows, only
    when it is meaningfully smaller."""

    def test_a_large_photo_gets_a_smaller_jpeg(self, tmp_path):
        pixels = (np.random.default_rng(5).random((2400, 3600, 3)) * 80 + 90).astype(np.uint8)
        path = samples.write_jpeg(str(tmp_path / "photo.jpg"), pixels=pixels, quality=92)
        original = os.path.getsize(path)
        assert original < LIMIT
        result = _generate(path, tmp_path, downscale=True)
        assert result.path.endswith("photo.jpg.previewFile.jpg")
        assert os.path.getsize(result.path) <= original * 0.75
        assert max(Image.open(result.path).size) <= 2048

    def test_a_small_png_gets_no_preview(self, tmp_path):
        path = samples.write_png(str(tmp_path / "small.png"))
        result = _generate(path, tmp_path, downscale=True)
        assert result.path is None and "No smaller preview possible" in result.message
        assert not [p for p in os.listdir(tmp_path) if ".previewFile." in p]

    def test_an_image_over_the_edge_trigger_is_written_even_when_not_smaller(self, tmp_path):
        # a flat colour compresses to almost nothing, so no 25 % saving is possible
        flat = np.full((2600, 2600, 3), 120, np.uint8)
        path = samples.write_png(str(tmp_path / "flat.png"), pixels=flat)
        result = _generate(path, tmp_path, downscale=True)
        assert result.path is not None
        assert max(Image.open(result.path).size) <= 2048

    def test_an_animated_gif_is_left_alone(self, tmp_path):
        path = samples.write_animated_gif(str(tmp_path / "anim.gif"), count=6)
        result = _generate(path, tmp_path, downscale=True)
        assert result.path is None and "own preview" in result.message

    def test_the_option_reads_the_environment_and_is_off_when_missing(self):
        assert media_preview.downscale_option_enabled({}) is False
        assert media_preview.downscale_option_enabled({media_preview.DOWNSCALE_ENV: "true"}) is True
        assert media_preview.downscale_option_enabled({media_preview.DOWNSCALE_ENV: "TRUE "}) is True
        for value in ("false", "", "1", "yes"):
            assert media_preview.downscale_option_enabled({media_preview.DOWNSCALE_ENV: value}) is False


@pytest.mark.unit
class TestStillImagesOverTheLimit:
    def test_a_large_png_becomes_a_jpeg_under_the_limit(self, tmp_path):
        path = samples.write_png_of_size(str(tmp_path / "big.png"), LIMIT + 1_000_000)
        result = _generate(path, tmp_path)
        assert result.path.endswith("big.png.previewFile.jpg")
        assert os.path.getsize(result.path) < LIMIT
        assert Image.open(result.path).format == "JPEG"

    def test_exif_orientation_is_applied(self, tmp_path):
        # a 120 x 40 image stored with orientation 6 (rotate 90 clockwise to display) is 40 x 120
        pixels = np.zeros((40, 120, 3), np.uint8)
        pixels[:, :60] = (255, 0, 0)
        path = samples.write_jpeg(str(tmp_path / "turned.jpg"), pixels=pixels, exif_orientation=6)
        image = image_handler.load_raster(path, ".jpg", BUDGET)
        assert image.size == (40, 120)

    @pytest.mark.parametrize("orientation,size", [(1, (120, 40)), (3, (120, 40)), (6, (40, 120)),
                                                  (8, (40, 120)), (5, (40, 120)), (2, (120, 40))])
    def test_every_orientation(self, tmp_path, orientation, size):
        path = samples.write_jpeg(str(tmp_path / "o.jpg"), pixels=samples.gradient(120, 40),
                                  exif_orientation=orientation)
        assert image_handler.load_raster(path, ".jpg", BUDGET).size == size

    def test_transparency_is_composited_on_white(self, tmp_path):
        rgba = np.zeros((40, 40, 4), np.uint8)
        rgba[:, 20:] = (0, 0, 255, 255)  # right half opaque blue, left half fully transparent
        Image.fromarray(rgba, "RGBA").save(tmp_path / "t.png")
        # force the generated route: a PNG this small is self-previewable, so call the encoder directly
        image = image_handler.load_raster(str(tmp_path / "t.png"), ".png", BUDGET)
        out = image_utils.save_jpeg_under_limit(image, str(tmp_path / "t.jpg"))
        with Image.open(out) as jpeg:
            assert jpeg.getpixel((5, 20)) == pytest.approx((255, 255, 255), abs=3)
            assert jpeg.getpixel((35, 20))[2] > 240 and jpeg.getpixel((35, 20))[0] < 20

    def test_a_palette_png_with_transparency_goes_on_white(self, tmp_path):
        image = Image.new("P", (20, 20), 0)
        image.putpalette([0, 0, 0, 255, 0, 0] + [0] * 762)
        image.info["transparency"] = 0
        image.save(tmp_path / "p.png", transparency=0)
        loaded = image_handler.load_raster(str(tmp_path / "p.png"), ".png", BUDGET)
        assert loaded.mode == "RGBA"
        out = image_utils.save_jpeg_under_limit(loaded, str(tmp_path / "p.jpg"))
        assert Image.open(out).getpixel((10, 10)) == pytest.approx((255, 255, 255), abs=3)

    def test_the_jpeg_ladder_drops_quality_then_size_and_stays_under_a_tight_limit(self, tmp_path,
                                                                                   monkeypatch):
        monkeypatch.setattr(image_utils, "MAX_FILE_SIZE_BYTES", 60_000)
        path = samples.write_png_of_size(str(tmp_path / "n.png"), 400_000)
        result = _generate(path, tmp_path)
        assert os.path.getsize(result.path) < 60_000

    def test_a_jpeg_never_exceeds_the_long_edge_it_was_given(self, tmp_path):
        out = image_utils.save_jpeg_under_limit(
            Image.fromarray(samples.gradient(3000, 1000)), str(tmp_path / "e.jpg"), max_long_edge=1000)
        assert max(Image.open(out).size) == 1000

    def test_an_image_is_never_scaled_up(self, tmp_path):
        out = image_utils.save_jpeg_under_limit(
            Image.fromarray(samples.gradient(100, 50)), str(tmp_path / "s.jpg"))
        assert Image.open(out).size == (100, 50)

    def test_output_type_png_writes_a_png_when_one_fits(self, tmp_path):
        path = samples.write_png_of_size(str(tmp_path / "big.png"), LIMIT + 200_000)
        result = _generate(path, tmp_path, output_type="png")
        assert result.path.endswith(".previewFile.png") or result.path.endswith(".previewFile.jpg")
        assert os.path.getsize(result.path) < LIMIT

    def test_output_type_gif_on_a_still_image_writes_a_jpeg(self, tmp_path):
        path = samples.write_png_of_size(str(tmp_path / "big.png"), LIMIT + 200_000)
        result = _generate(path, tmp_path, output_type="gif")
        assert result.path.endswith(".previewFile.jpg")

    def test_a_png_that_does_not_fit_falls_back_to_a_jpeg(self, tmp_path, monkeypatch):
        monkeypatch.setattr(image_utils, "MAX_FILE_SIZE_BYTES", 30_000)
        path = samples.write_png_of_size(str(tmp_path / "n.png"), 400_000)
        result = _generate(path, tmp_path, output_type="png")
        assert result.path.endswith(".previewFile.jpg") and os.path.getsize(result.path) < 30_000


@pytest.mark.unit
class TestTgaAndTiff:
    def test_tga_variants(self, tmp_path):
        for name, kwargs in (("raw.tga", {}), ("rle.tga", {"rle": True}), ("alpha.tga", {"rgba": True})):
            path = samples.write_tga(str(tmp_path / name), **kwargs)
            result = _generate(path, tmp_path)
            assert result.path.endswith(f"{name}.previewFile.jpg"), name
            assert os.path.getsize(result.path) < LIMIT
            assert _center(result.path)[0] == (64, 48)

    @pytest.mark.parametrize("writer,name", [
        (samples.write_tiff_pillow, "lzw.tif"),
        (samples.write_tiff_16bit_rgb, "rgb16.tif"),
        (samples.write_tiff_float, "float.tiff"),
        (samples.write_tiff_float_rgb, "floatrgb.tif"),
        (samples.write_tiff_multipage, "multi.tif"),
        (samples.write_tiff_bigtiff_tiled, "bigtiled.tif"),
        (samples.write_tiff_planar, "planar.tif"),
        (samples.write_tiff_cmyk, "cmyk.tif"),
        (samples.write_geotiff, "geo.tif"),
    ])
    def test_tiff_variants_become_a_jpeg(self, tmp_path, writer, name):
        path = writer(str(tmp_path / name))
        result = _generate(path, tmp_path)
        assert result.path.endswith(f"{name}.previewFile.jpg")
        assert os.path.getsize(result.path) < LIMIT

    def test_a_16_bit_tiff_is_scaled_to_8_bit_with_plausible_colours(self, tmp_path):
        path = samples.write_tiff_16bit_rgb(str(tmp_path / "rgb16.tif"))
        image = image_handler.load_raster(path, ".tif", BUDGET)
        assert image.mode == "RGB" and image.getpixel((32, 24)) == pytest.approx((128, 128, 90), abs=6)

    def test_a_float_tiff_is_percentile_scaled_and_survives_nan_and_inf(self, tmp_path):
        path = samples.write_tiff_float(str(tmp_path / "f.tif"))
        image = image_handler.load_raster(path, ".tif", BUDGET)
        gray = np.asarray(image.convert("L"))
        assert gray.max() > 200 and gray.min() < 30  # the range is stretched, not left at 0..1

    def test_a_cmyk_tiff_is_converted_to_rgb(self, tmp_path):
        path = samples.write_tiff_cmyk(str(tmp_path / "c.tif"), cmyk=(0, 255, 255, 0))
        image = image_handler.load_raster(path, ".tif", BUDGET)
        assert image.mode == "RGB"
        assert image.getpixel((10, 10)) == pytest.approx((255, 0, 0), abs=12)

    def test_a_multi_page_tiff_uses_the_first_full_resolution_page(self, tmp_path):
        path = samples.write_tiff_multipage(str(tmp_path / "m.tif"), width=64, height=48)
        image = image_handler.load_raster(path, ".tif", BUDGET)
        assert image.size == (64, 48)

    def test_the_tifffile_fallback_reads_what_pillow_cannot(self, tmp_path):
        path = samples.write_tiff_float_rgb(str(tmp_path / "float-rgb.tif"))
        with pytest.raises(Exception):
            with Image.open(path) as img:
                img.load()
        image = image_handler.load_raster(path, ".tif", BUDGET)
        assert image.size == (64, 48) and image.mode == "RGB"
        # the scaling is shared by the three channels, so the colours keep their balance
        assert image.getpixel((32, 24)) == pytest.approx((128, 128, 90), abs=10)

    def test_a_tiff_with_an_orientation_tag_is_made_upright(self, tmp_path):
        from PIL import TiffImagePlugin
        info = TiffImagePlugin.ImageFileDirectory_v2()
        info[274] = 6
        Image.fromarray(samples.gradient(60, 20)).save(str(tmp_path / "o.tif"), "TIFF", tiffinfo=info)
        assert image_handler.load_raster(str(tmp_path / "o.tif"), ".tif", BUDGET).size == (20, 60)


@pytest.mark.unit
class TestUntrustedImages:
    def test_a_decompression_bomb_header_is_refused_before_decoding(self, tmp_path, monkeypatch):
        # A 30000 x 30000 PNG header (900 million pixels) over a 100 million pixel budget; the file is
        # never decoded, so this is cheap.
        bomb = tmp_path / "bomb.png"
        Image.new("1", (30000, 30000)).save(bomb, "PNG")
        decoded = []
        monkeypatch.setattr(Image.Image, "load", lambda self, *a, **k: decoded.append(1))
        with pytest.raises(ImageTooLarge) as caught:
            image_handler.load_raster(str(bomb), ".png", 100_000_000)
        assert "PNG" in str(caught.value) and "900,000,000" in str(caught.value)
        assert not decoded

    def test_the_bomb_reaches_the_run_as_a_message_not_a_stack_trace(self, tmp_path):
        bomb = tmp_path / "bomb.png"
        Image.new("1", (30000, 30000)).save(bomb, "PNG")
        # over the limit so the run decodes it rather than treating it as its own preview
        with open(bomb, "ab") as handle:
            handle.write(b"\0" * LIMIT)
        with pytest.raises(ImageTooLarge):
            _generate(str(bomb), tmp_path, budget=100_000_000)

    def test_a_tiff_bomb_header_is_refused_on_both_readers(self, tmp_path):
        import tifffile
        path = str(tmp_path / "bomb.tif")
        tifffile.imwrite(path, np.zeros((20000, 20000), np.uint8), compression="zlib")
        with pytest.raises(ImageTooLarge):
            image_handler.load_raster(path, ".tif", 100_000_000)

    def test_a_corrupt_image_names_its_type(self, tmp_path):
        path = tmp_path / "bad.tga"
        path.write_bytes(os.urandom(64))
        with pytest.raises(MediaError) as caught:
            _generate(str(path), tmp_path)
        assert "TGA" in str(caught.value)
        assert "Traceback" not in str(caught.value)

    def test_a_truncated_tiff_fails_with_a_message(self, tmp_path):
        good = samples.write_tiff_bigtiff_tiled(str(tmp_path / "good.tif"))
        data = open(good, "rb").read()
        (tmp_path / "cut.tif").write_bytes(data[: len(data) // 3])
        with pytest.raises(MediaError) as caught:
            _generate(str(tmp_path / "cut.tif"), tmp_path)
        assert "TIFF" in str(caught.value)

    def test_a_zero_length_tiff_fails_with_a_message(self, tmp_path):
        (tmp_path / "zero.tif").write_bytes(b"")
        with pytest.raises(MediaError) as caught:
            _generate(str(tmp_path / "zero.tif"), tmp_path)
        assert "TIFF" in str(caught.value)

    def test_the_pixel_budget_is_set_from_the_task_memory(self):
        from preview_pipeline import resources
        small = resources.image_pixel_budget(2 * 1024 ** 3)
        large = resources.image_pixel_budget(120 * 1024 ** 3)
        assert resources.MIN_IMAGE_PIXELS <= small < large <= resources.MAX_IMAGE_PIXELS
        assert resources.compute_budgets(memory_bytes=16 * 1024 ** 3, cpus=4).pixels == \
            resources.image_pixel_budget(16 * 1024 ** 3)
        assert f"pixels={resources.image_pixel_budget(16 * 1024 ** 3):,}" in \
            resources.compute_budgets(memory_bytes=16 * 1024 ** 3, cpus=4).describe()

    def test_pillows_own_bomb_limit_is_restored_after_a_load(self, tmp_path):
        before = Image.MAX_IMAGE_PIXELS
        image_handler.load_raster(samples.write_tga(str(tmp_path / "a.tga")), ".tga", 12_345_678)
        assert Image.MAX_IMAGE_PIXELS == before


@pytest.mark.unit
class TestSizeLimitIsStrict:
    def test_the_existing_ladder_requires_strictly_under(self, tmp_path):
        """A GIF of exactly the limit does not fit; the 3D ladder moved from <= to <."""
        frames = [np.full((8, 8, 3), i * 20, np.uint8) for i in range(4)]
        out = str(tmp_path / "x.gif")
        image_utils.save_gif(frames, out)
        exact = os.path.getsize(out)
        returned = image_utils.ensure_under_size_limit(frames, out, max_bytes=exact)
        assert returned != out or os.path.getsize(returned) < exact
        assert os.path.getsize(returned) < exact
        assert image_utils.ensure_under_size_limit(frames, out, max_bytes=exact + 1) == out
