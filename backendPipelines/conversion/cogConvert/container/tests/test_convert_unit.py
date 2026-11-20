# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Naming, limits, the GDAL command, the child environment and the attribute documents."""

import collections
import json

import pytest

from cog_convert_pipeline import convert
from cog_convert_pipeline.tiff_header import RasterHeader


def _header(**kw):
    base = dict(width=100, height=100, band_count=3, bits_per_sample=8, big_tiff=False, tiled=True,
                overview_count=0, compression="deflate", georeferenced=True, crs="EPSG:4326", epsg=4326,
                is_cog=False, raw_bytes=30000)
    base.update(kw)
    return RasterHeader(**base)


@pytest.mark.unit
class TestNaming:
    def test_the_suffix_is_appended_to_the_whole_name(self):
        assert convert.derived_output_name("a.tif") == "a.tif.cog.tif"
        assert convert.derived_output_name("a.tiff") == "a.tiff.cog.tif"

    def test_tif_and_tiff_in_one_folder_cannot_collide(self):
        assert convert.derived_output_name("a.tif") != convert.derived_output_name("a.tiff")

    @pytest.mark.parametrize("key,asset,subdir", [
        ("xA1/scene.tif", "xA1", ""),
        ("xA1/maps/2026/scene.tiff", "xA1", "maps/2026"),
        ("other/scene.tif", "xA1", ""),
        ("xA1/maps/scene.tif", "", ""),
    ])
    def test_relative_subdir(self, key, asset, subdir):
        assert convert.relative_subdir(key, asset) == subdir

    def test_join_key_drops_empty_segments(self):
        assert convert.join_key("/p/files/", "", "d", "x.tif.cog.tif") == "p/files/d/x.tif.cog.tif"

    def test_asset_relative_path_has_a_leading_slash(self):
        assert convert.asset_relative_path("maps", "a.tif") == "/maps/a.tif"
        assert convert.asset_relative_path("", "a.tif") == "/a.tif"


@pytest.mark.unit
class TestParams:
    def test_defaults(self):
        assert convert.parse_params("") == {"compression": "DEFLATE"}

    def test_a_lowercase_value_is_accepted(self):
        assert convert.parse_params('{"compression": "deflate"}')["compression"] == "DEFLATE"

    def test_an_unlisted_compression_is_refused(self):
        with pytest.raises(ValueError):
            convert.parse_params('{"compression": "JPEG"}')


@pytest.mark.unit
class TestOutputProfile:
    """The profile constants are the one place the output CRS lives."""

    def test_the_output_is_web_mercator_with_the_google_maps_scheme(self):
        assert convert.OUTPUT_CRS == "EPSG:3857"
        assert convert.TILING_SCHEME == "GoogleMapsCompatible"

    def test_the_command_carries_the_agreed_creation_options(self):
        cmd = convert.build_command("/w/in/a.tif", "/w/out/a.tif.cog.tif", {"compression": "DEFLATE"}, 4)
        assert cmd[:4] == ["gdal_translate", "-of", "COG", "-q"]
        options = [cmd[i + 1] for i, token in enumerate(cmd) if token == "-co"]
        for option in ("COMPRESS=DEFLATE", "PREDICTOR=YES", "OVERVIEWS=AUTO", "NUM_THREADS=4",
                       "TILING_SCHEME=GoogleMapsCompatible"):
            assert option in options
        assert cmd[-2:] == ["/w/in/a.tif", "/w/out/a.tif.cog.tif"]

    def test_without_a_tiling_scheme_the_native_block_size_is_used(self, monkeypatch):
        monkeypatch.setattr(convert, "TILING_SCHEME", None)
        options = convert.creation_options({"compression": "DEFLATE"}, 2)
        assert "BLOCKSIZE=512" in options and not any(o.startswith("TILING_SCHEME") for o in options)

    @pytest.mark.parametrize("path", ["relative/a.tif", "/vsicurl/http://x/a.tif", "/vsis3/b/k.tif"])
    def test_only_absolute_local_paths_reach_gdal(self, path):
        with pytest.raises(ValueError):
            convert.build_command(path, "/w/out.tif", {"compression": "DEFLATE"}, 1)

    def test_a_path_with_shell_text_stays_one_argument(self):
        name = "/w/in/a; touch x $(id).tif"
        cmd = convert.build_command(name, "/w/out/o.tif", {"compression": "DEFLATE"}, 1)
        assert name in cmd  # an argument list: no shell ever sees it

    def test_the_child_environment_is_confined(self, monkeypatch):
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "do-not-leak")
        monkeypatch.setenv("TASK_TOKEN", "tok")
        env = convert.gdal_env(8 * 1024 ** 3)
        assert "AWS_SECRET_ACCESS_KEY" not in env and "TASK_TOKEN" not in env
        assert env["GDAL_DISABLE_READDIR_ON_OPEN"] == "EMPTY_DIR"
        assert env["PROJ_NETWORK"] == "OFF"
        assert env["GDAL_CACHEMAX"] == str(4096)


@pytest.mark.unit
class TestLimits:
    def test_a_normal_file_passes(self):
        convert.check_limits(_header(), 10 ** 6)

    def test_too_many_pixels_is_refused_with_the_limit(self):
        with pytest.raises(convert.ConversionRefused, match="uncompressed"):
            convert.check_limits(_header(raw_bytes=convert.MAX_RAW_BYTES + 1), 10 ** 6)

    def test_too_many_bands_is_refused(self):
        with pytest.raises(convert.ConversionRefused, match="bands"):
            convert.check_limits(_header(band_count=convert.MAX_BANDS + 1), 10 ** 6)

    def test_too_large_an_input_is_refused(self):
        with pytest.raises(convert.ConversionRefused, match="GiB"):
            convert.check_limits(_header(), convert.MAX_INPUT_BYTES + 1)

    def test_the_output_estimate_covers_the_measured_worst_case(self):
        # Measured on a 152 MB JPEG-in-TIFF (16,716 x 16,716 RGB): the COG was 23,808 x 23,808 RGBA, 1.44 GB
        # after DEFLATE and 2.27 GB uncompressed. The estimate must be at least the uncompressed size, and the
        # scratch need at least temporary plus final (2.9 GB measured).
        header = _header(width=16716, height=16716, band_count=3)
        pixels = convert.estimate_output_pixels(16716, 16716, (21800, 21800))
        output_bytes = convert.estimate_output_bytes(header, pixels)
        assert output_bytes >= 23808 * 23808 * 4
        assert convert.required_scratch_bytes(output_bytes) >= int(2.9e9)

    def test_a_small_scratch_volume_is_refused_before_the_conversion(self, monkeypatch, tmp_path):
        # D2: a 152 MB compressed input passed the old input-size check on a 2 GiB volume and then ran out of
        # space inside GDAL. The estimate comes from the pixel grid, not from the file size.
        usage = collections.namedtuple("usage", "total used free")
        monkeypatch.setattr(convert.shutil, "disk_usage", lambda _p: usage(100, 90, 2 * 1024 ** 3))
        header = _header(width=16716, height=16716, band_count=3)
        output_bytes = convert.estimate_output_bytes(
            header, convert.estimate_output_pixels(16716, 16716, (21800, 21800)))
        with pytest.raises(convert.ConversionRefused, match="only 2.0 GiB"):
            convert.check_disk(str(tmp_path), output_bytes, 152 * 1024 ** 2)

    def test_the_message_names_the_volume_and_does_not_tell_you_to_raise_it(self, monkeypatch, tmp_path):
        usage = collections.namedtuple("usage", "total used free")
        monkeypatch.setattr(convert.shutil, "disk_usage", lambda _p: usage(100, 90, 1024 ** 3))
        with pytest.raises(convert.ConversionRefused) as refused:
            convert.check_disk(str(tmp_path), 50 * 1024 ** 3, 10 * 1024 ** 3)
        assert "200 GiB" in str(refused.value) and "raise" not in str(refused.value).lower()

    def test_a_degenerate_warped_grid_falls_back_to_the_source_grid(self):
        # A whole-world raster warps to a few pixels; the source grid then sets the bound.
        assert convert.estimate_output_pixels(360, 180, (33, 401)) >= 360 * 180 * 2
        assert convert.estimate_output_pixels(360, 180, None) >= 360 * 180 * 2

    def test_the_input_that_cannot_fit_is_refused_before_the_download(self, monkeypatch, tmp_path):
        usage = collections.namedtuple("usage", "total used free")
        monkeypatch.setattr(convert.shutil, "disk_usage", lambda _p: usage(100, 90, 150 * 1024 ** 3))
        convert.check_download_disk(str(tmp_path), 100 * 1024 ** 3)  # the documented maximum input fits
        with pytest.raises(convert.ConversionRefused, match="200 GiB"):
            convert.check_download_disk(str(tmp_path), 149.5 * 1024 ** 3)

    def test_memory_takes_the_smallest_bound(self, monkeypatch):
        monkeypatch.setenv("VAMS_TASK_MEMORY_MIB", "1024")
        assert convert.task_memory_bytes() <= 1024 ** 3


@pytest.mark.unit
class TestAttributes:
    def test_the_cog_documents_its_source_and_its_crs(self):
        doc = json.loads(convert.cog_attributes("/maps/a.tif", "v1", "DEFLATE", "3.13.3", "EPSG:32633",
                                                 "EPSG:3857"))
        rows = {r["metadataKey"]: r["metadataValue"] for r in doc["metadata"]}
        assert doc["type"] == "attribute" and doc["updateType"] == "update"
        assert rows == {
            "cog_link_cogOf": "/maps/a.tif", "cog_link_cogOfVersionId": "v1",
            "cog_link_cogCompression": "deflate", "cog_link_cogToolVersion": "gdal 3.13.3",
            "cog_link_sourceCrs": "EPSG:32633", "cog_link_outputCrs": "EPSG:3857"}
        assert all(r["metadataValueType"] == "string" for r in doc["metadata"])

    def test_no_version_row_without_a_version(self):
        doc = json.loads(convert.cog_attributes("/a.tif", "", "DEFLATE", "3", "EPSG:4326", "EPSG:3857"))
        assert "cog_link_cogOfVersionId" not in {r["metadataKey"] for r in doc["metadata"]}

    def test_the_source_names_its_cog(self):
        doc = json.loads(convert.source_attributes("/maps/a.tif.cog.tif"))
        assert doc["metadata"] == [{"metadataKey": "cog_link_cogFile",
                                    "metadataValue": "/maps/a.tif.cog.tif", "metadataValueType": "string"}]


@pytest.mark.unit
class TestStreaming:
    def test_the_tail_is_bounded(self):
        code, tail = convert.run_streaming(
            ["python3", "-c", "print('\\n'.join(str(i) for i in range(500)))"], tail_lines=10)
        assert code == 0 and len(tail.splitlines()) == 10 and tail.splitlines()[-1] == "499"

    def test_a_failing_child_returns_its_code(self):
        code, _ = convert.run_streaming(["python3", "-c", "import sys; sys.exit(3)"])
        assert code == 3
