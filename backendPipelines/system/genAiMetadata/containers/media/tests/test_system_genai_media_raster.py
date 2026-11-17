#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The GeoTIFF / COG extractor (`media_extractors/raster.py`): every fixture of the shared raster set against its
`.expected.json` sidecar, the read limits against hostile headers, the derived-COG rule, and the handler's ranged
read (no download, no pixel).

The fixtures are generated with GDAL (`tests/fixtures/raster/generate_fixtures.py`) and are the same set the viewer
and TIFF-to-COG cards test against. GDAL's own `wgs84Extent` is the oracle for the WGS84 bounds."""

import glob
import io
import json
import os
import struct

import pytest

from media_extractors import raster
from media_extractors.common import CLASS_OTHER, CLASS_RASTER, ExtractContext
from media_extractors.s3_ranged import RangedS3Reader
from system_genai_media_fixtures import FakeS3
from test_system_genai_media_handler import (
    _ASSETS, _AUX, _CONFIG_BODY, _CONFIG_KEY, _MANIFEST_KEY, _RUN, _event, _load, _pre_manifest,
)

pytestmark = pytest.mark.unit

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "raster")
FIXTURES = sorted(os.path.basename(path)[:-len(".expected.json")]
                  for path in glob.glob(os.path.join(FIXTURE_DIR, "*.expected.json")))


def _ctx(name="scene.tif", **kwargs):
    return ExtractContext(file_name=name, extension=os.path.splitext(name)[1], content_type="image/tiff",
                          max_text_chars=4000, work_dir="/tmp", **kwargs)


def _bytes(name):
    with open(os.path.join(FIXTURE_DIR, name + ".tif"), "rb") as handle:
        return handle.read()


def _expected(name):
    with open(os.path.join(FIXTURE_DIR, name + ".expected.json"), encoding="utf-8") as handle:
        return json.load(handle)


def _extract(data, name="scene.tif", **kwargs):
    return raster.extract_raster(io.BytesIO(data), len(data), _ctx(name, **kwargs))


# --- a minimal TIFF writer for the hostile cases ----------------------------------------------------------------

SHORT, LONG = 3, 4


def _classic(entries, *, next_ifd=0, pad_to=0, extra=b"", ifd_at=8):
    """A little-endian classic TIFF with one directory at `ifd_at`. `entries` are (tag, type, count, value) with the
    value inline (a number) or ('at', offset) for an out-of-line value. `extra` is placed after the directory."""
    entries = sorted(entries)
    table = struct.pack("<H", len(entries))
    for tag, dtype, count, value in entries:
        raw = struct.pack("<I", value[1]) if isinstance(value, tuple) else (
            struct.pack("<H", value) + b"\0\0" if dtype == SHORT and count == 1 else struct.pack("<I", value))
        table += struct.pack("<HHI", tag, dtype, count) + raw
    table += struct.pack("<I", next_ifd)
    body = b"II*\0" + struct.pack("<I", ifd_at) + b"\0" * (ifd_at - 8) + table + extra
    return body + b"\0" * max(0, pad_to - len(body))


def _basic(width=100, height=80, overrides=None):
    entries = {256: (256, LONG, 1, width), 257: (257, LONG, 1, height), 258: (258, SHORT, 1, 8),
               259: (259, SHORT, 1, 1), 262: (262, SHORT, 1, 1), 273: (273, LONG, 1, 2000),
               277: (277, SHORT, 1, 1), 278: (278, LONG, 1, height), 279: (279, LONG, 1, min(width * height, 2 ** 32 - 1))}
    entries.update(overrides or {})
    return list(entries.values())


class TestFixtureMatrix:
    def test_the_fixture_set_is_present(self):
        # Control: the matrix below is vacuous with no fixtures.
        assert len(FIXTURES) >= 20
        for required in ("cog_4326", "striped_4326", "cog_bigtiff_4326", "epsg3857", "utm33", "polar_3031_pole",
                         "antimeridian_3832", "rotated_utm33", "no_georef", "user_defined_laea",
                         "nodata_float32_4326", "palette_4326", "uint16_4326", "compress_zstd", "compress_jpeg",
                         "compress_lzw", "compress_deflate", "pixel_is_point_4326"):
            assert required in FIXTURES, required

    @pytest.mark.parametrize("name", FIXTURES)
    def test_facts_match_the_sidecar(self, name):
        expected = _expected(name)
        result = _extract(_bytes(name), name + ".tif")
        assert result.file_class == CLASS_RASTER and result.warnings == []
        facts = result.attributes["sys_raster"]
        for key in ("width", "height", "bandCount", "dataType", "compression", "layout", "blockWidth", "blockHeight",
                    "overviewCount", "isCog", "bigTiff", "georeferenced", "crs"):
            assert facts[key] == expected[key], (name, key)
        assert facts.get("nodata") == expected["nodata"]
        assert facts.get("rotated", False) == expected["rotated"]
        assert facts.get("pixelIsPoint", False) == expected["pixelIsPoint"]
        if expected["pixelSizeX"] is not None and not expected["rotated"]:
            assert facts["pixelSizeX"] == pytest.approx(expected["pixelSizeX"])
            assert facts["pixelSizeY"] == pytest.approx(expected["pixelSizeY"])
        if expected["nativeBounds"] is not None:
            assert facts["nativeBounds"] == pytest.approx(expected["nativeBounds"])

    @pytest.mark.parametrize("name", FIXTURES)
    def test_the_footprint_matches_gdals_wgs84_extent(self, name):
        expected = _expected(name)
        result = _extract(_bytes(name), name + ".tif")
        facts, geo = result.attributes["sys_raster"], result.attributes["sys_geo"]
        assert geo["format"] == "GeoTIFF"
        if expected["footprint"] is None:
            assert geo["footprint"] is None and facts["footprintNote"] == expected["footprintNote"]
            assert "wgs84Bounds" not in facts
            return
        assert geo["footprint"]["type"] == expected["footprint"] and "footprintNote" not in facts
        assert geo["crs"] == expected["crs"]
        if expected["wgs84Bounds"] is not None:
            assert facts["wgs84Bounds"] == pytest.approx(expected["wgs84Bounds"], abs=0.005)

    def test_the_antimeridian_fixture_is_a_multipolygon_marked_as_crossing(self):
        result = _extract(_bytes("antimeridian_3832"))
        assert result.attributes["sys_geo"]["footprint"]["type"] == "MultiPolygon"
        assert result.attributes["sys_raster"]["crossesAntimeridian"] is True
        # GDAL's extent agrees with the two edges (178.746 east, -178.954 west).
        assert result.attributes["sys_raster"]["wgs84Bounds"] == pytest.approx(
            [178.7460891, 6.7128922, -178.9542238, 9.0058826], abs=0.005)

    def test_a_pixel_is_point_file_is_shifted_by_half_a_pixel(self):
        # The tie point is the first pixel's centre (30.005, 19.995); the edge is half a pixel further out.
        result = _extract(_bytes("pixel_is_point_4326"))
        assert result.attributes["sys_raster"]["wgs84Bounds"] == pytest.approx([30.0, 17.44, 32.56, 20.0])

    def test_the_footprint_is_a_valid_location_polygon(self):
        ring = _extract(_bytes("utm33")).attributes["sys_geo"]["footprint"]["coordinates"][0]
        assert ring[0] == ring[-1] and len(ring) > 5
        assert all(a != b for a, b in zip(ring, ring[1:]))
        assert all(-180 <= lon <= 180 and -90 <= lat <= 90 for lon, lat in ring)

    def test_facts_and_summary_lines(self):
        result = _extract(_bytes("cog_4326"))
        assert result.facts["size"] == "1,024 x 1,024 pixels"
        assert result.facts["layout"] == "Cloud Optimized GeoTIFF" and result.facts["crs"] == "EPSG:4326"
        assert result.facts["bands"] == "1 band, uint8" and result.facts["footprint"] == "Polygon"
        assert _extract(_bytes("striped_4326")).facts["layout"] == "striped GeoTIFF"
        assert _extract(_bytes("no_georef")).facts["footprint"] == "none (not-georeferenced)"
        assert _extract(_bytes("polar_3031_pole")).facts["footprint"] == "none (contains-pole)"

    def test_no_pixel_is_requested_from_the_file(self):
        data = _bytes("cog_4326")
        client = _RangeClient(data)
        reader = RangedS3Reader(client, "b", "k", size=len(data))
        raster.extract_raster(reader, len(data), _ctx())
        first_tile = min(__import__("tifffile").TiffFile(io.BytesIO(data)).pages[0].dataoffsets)
        # The header sits before the first tile; the read-ahead may run into the first tile's bytes (18 KiB file)
        # but no request names a range beyond the file or a second round trip for pixels.
        assert client.gets == 1 and first_tile < len(data)

    def test_a_derived_cog_name_needs_the_source_beside_it(self):
        assert raster.derived_cog_source_name("a/b.tif.cog.tif") == "a/b.tif"
        assert raster.derived_cog_source_name("b.TIFF.COG.TIF") == "b.TIFF"
        assert raster.derived_cog_source_name("b.cog.tif") is None
        assert raster.derived_cog_source_name("b.tif") is None

    def test_a_cog_with_its_source_beside_it_is_a_derived_cog_that_keeps_its_facts(self):
        result = _extract(_bytes("cog_small_4326"), "pump.tif.cog.tif", derived_source_exists=lambda: True)
        facts = result.attributes["sys_raster"]
        assert facts["derivedCog"] is True and facts["isCog"] is True
        assert result.attributes["sys_geo"]["footprint"]["type"] == "Polygon"

    def test_a_cog_without_its_source_is_an_original(self):
        result = _extract(_bytes("cog_small_4326"), "pump.tif.cog.tif", derived_source_exists=lambda: False)
        assert "derivedCog" not in result.attributes["sys_raster"]

    def test_a_failed_source_check_leaves_the_file_unflagged_with_a_warning(self):
        def broken():
            raise OSError("boom")

        result = _extract(_bytes("cog_small_4326"), "pump.tif.cog.tif", derived_source_exists=broken)
        assert "derivedCog" not in result.attributes["sys_raster"]
        assert any("could not be checked" in warning for warning in result.warnings)

    def test_the_source_check_is_not_made_for_an_ordinary_name(self):
        calls = []
        _extract(_bytes("cog_small_4326"), "pump.tif", derived_source_exists=lambda: calls.append(1) or True)
        assert calls == []


class _RangeClient:
    def __init__(self, data):
        self.data, self.gets = data, 0

    def get_object(self, Bucket, Key, Range, **kwargs):
        first, _, last = Range[len("bytes="):].partition("-")
        self.gets += 1
        return {"Body": io.BytesIO(self.data[int(first):int(last) + 1])}


class TestHostileHeaders:
    def test_bytes_that_are_not_a_tiff_fall_back_to_other_with_a_warning(self):
        result = _extract(b"This is a note saved as .tif\n" * 4)
        assert result.file_class == CLASS_OTHER and result.attributes == {}
        assert "not a TIFF file" in result.warnings[0] and "sys_file attributes only" in result.warnings[0]

    @pytest.mark.parametrize("data", [b"", b"II", b"II*\0", b"MM\0*\0\0\0\x08"])
    def test_a_file_cut_inside_the_header_falls_back(self, data):
        result = _extract(data)
        assert result.file_class == CLASS_OTHER and result.warnings

    def test_a_directory_chain_that_loops_is_refused(self):
        data = _classic(_basic(), next_ifd=8)
        result = _extract(data)
        assert result.file_class == CLASS_OTHER and "loops" in result.warnings[0]

    def test_a_directory_with_more_entries_than_the_limit_is_refused(self):
        head = b"II*\0" + struct.pack("<I", 8) + struct.pack("<H", raster.MAX_IFD_ENTRIES + 1)
        data = head + b"\0" * (12 * (raster.MAX_IFD_ENTRIES + 1) + 4)
        result = _extract(data)
        assert result.file_class == CLASS_OTHER and "entries" in result.warnings[0]

    def test_a_directory_running_past_the_end_of_the_file_is_refused(self):
        data = b"II*\0" + struct.pack("<I", 8) + struct.pack("<H", 40) + b"\0" * 30
        assert _extract(data).file_class == CLASS_OTHER

    def test_a_directory_offset_outside_the_file_is_refused(self):
        data = b"II*\0" + struct.pack("<I", 0x7FFFFFF0) + b"\0" * 8
        result = _extract(data)
        assert result.file_class == CLASS_OTHER and "outside the file" in result.warnings[0]

    def test_a_tag_that_claims_more_bytes_than_the_file_holds_is_refused(self):
        hostile = _basic(overrides={273: (273, LONG, 0x30000000, ("at", 100))})
        result = _extract(_classic(hostile, pad_to=4096))
        assert result.file_class == CLASS_OTHER and "outside the file" in result.warnings[0]

    @pytest.mark.parametrize("width,height", [(0, 10), (10, 0), (raster.MAX_DIMENSION + 1, 10), (10, 2 ** 31)])
    def test_dimensions_that_are_not_sane_are_refused_with_a_message(self, width, height):
        result = _extract(_classic(_basic(width, height), pad_to=4096))
        assert result.file_class == CLASS_OTHER and "image size is not usable" in result.warnings[0]

    def test_a_missing_image_size_is_refused(self):
        entries = [entry for entry in _basic() if entry[0] != 256]
        assert _extract(_classic(entries, pad_to=4096)).file_class == CLASS_OTHER

    def test_a_huge_strip_offset_array_records_the_layout_and_truncation_not_a_failure(self):
        count = 2_000_000  # 8 MB of LONG offsets, over the 4 MiB per-array bound
        size = 8 + 2 + 12 * 9 + 4 + 9_500_000
        entries = _basic(overrides={273: (273, LONG, count, ("at", 200)), 279: (279, LONG, count, ("at", 200))})
        data = _classic(entries, pad_to=size)
        result = _extract(data)
        facts = result.attributes["sys_raster"]
        assert result.file_class == CLASS_RASTER
        assert facts["layout"] == "striped" and facts["offsetsTruncated"] is True and facts["isCog"] is False
        assert facts["width"] == 100 and facts["height"] == 80 and facts["compression"] == "none"
        assert result.attributes["sys_geo"]["footprint"] is None and facts["footprintNote"] == "header-too-large"
        assert any("over the read limits" in warning for warning in result.warnings)

    def test_a_chain_of_more_directories_than_the_limit_reads_the_first_ones(self):
        # 70 directories, each a complete one-strip image, chained.
        entry_count = 9
        stride = 2 + 12 * entry_count + 4
        pieces = []
        for index in range(70):
            at = 8 + index * stride
            following = at + stride if index < 69 else 0
            pieces.append((at, following))
        out = bytearray(b"II*\0" + struct.pack("<I", 8))
        for at, following in pieces:
            entries = _basic(10, 10, {273: (273, LONG, 1, 5000), 279: (279, LONG, 1, 100)})
            table = struct.pack("<H", len(entries))
            for tag, dtype, count, value in sorted(entries):
                raw = struct.pack("<H", value) + b"\0\0" if dtype == SHORT else struct.pack("<I", value)
                table += struct.pack("<HHI", tag, dtype, count) + raw
            table += struct.pack("<I", following)
            out += table
        out += b"\0" * 6000
        result = _extract(bytes(out))
        assert result.file_class == CLASS_RASTER
        assert result.attributes["sys_raster"]["ifdsTruncated"] is True
        assert any("more than 64 images" in warning for warning in result.warnings)

    def test_a_tag_value_stored_inside_another_tags_array_does_not_fool_the_cog_check(self):
        # A tiled-looking header whose IFD sits after its data is never a COG.
        result = _extract(_bytes("striped_4326"))
        assert result.attributes["sys_raster"]["isCog"] is False

    def test_the_ranged_read_budget_stops_a_header_that_needs_more_than_it_may_read(self):
        data = _bytes("cog_4326")
        client = _RangeClient(data)
        reader = RangedS3Reader(client, "b", "k", size=len(data), max_bytes=1024, max_gets=1, read_ahead=64)
        result = raster.extract_raster(reader, len(data), _ctx())
        assert result.file_class == CLASS_OTHER and "RangedReadBudgetExceeded" in result.warnings[0]

    def test_a_truncated_cog_does_not_crash(self):
        data = _bytes("cog_4326")
        for cut in (100, 300, 700):
            result = _extract(data[:cut])
            assert result.file_class in (CLASS_OTHER, CLASS_RASTER)

    def test_flipped_bytes_in_a_real_header_never_raise(self):
        data = bytearray(_bytes("cog_small_4326"))
        for position in range(0, 400, 7):
            damaged = bytearray(data)
            damaged[position] ^= 0xFF
            result = _extract(bytes(damaged))
            assert result.file_class in (CLASS_OTHER, CLASS_RASTER)


class TestIsCogDefinition:
    @pytest.mark.parametrize("name", FIXTURES)
    def test_is_cog_follows_the_four_rules(self, name):
        expected = _expected(name)
        facts = _extract(_bytes(name), name + ".tif").attributes["sys_raster"]
        assert facts["isCog"] is expected["isCog"]

    def test_a_large_tiled_file_without_overviews_is_not_a_cog(self):
        facts = _extract(_bytes("tiled_big_no_overviews_4326")).attributes["sys_raster"]
        assert facts["layout"] == "tiled" and facts["overviewCount"] == 0 and facts["isCog"] is False

    def test_a_directory_after_the_pixel_data_is_not_a_cog(self):
        facts = _extract(_bytes("pixel_is_point_4326")).attributes["sys_raster"]
        assert facts["layout"] == "tiled" and facts["isCog"] is False

    def test_a_small_tiled_file_needs_no_overview(self):
        facts = _extract(_bytes("tiled_no_overviews_4326")).attributes["sys_raster"]
        assert facts["overviewCount"] == 0 and facts["isCog"] is True


# --- the handler: a TIFF is read by range ----------------------------------------------------------------------

@pytest.fixture
def s3():
    fake = FakeS3()
    fake.objects[(_RUN, _CONFIG_KEY)] = json.dumps(_CONFIG_BODY).encode()
    return fake


def _seed(s3, name, data, file_class="raster", extra=None):
    manifest = _pre_manifest(file_class)
    s3.objects[(_AUX, _MANIFEST_KEY)] = json.dumps(manifest).encode()
    s3.objects[(_ASSETS, f"a1/{name}")] = data
    for key, value in (extra or {}).items():
        s3.objects[(_ASSETS, key)] = value


def _tiff_event(name, size):
    event = _event(name, "raster", "image/tiff")
    event["fileSize"] = size
    return event


def _range_log(module):
    lines = [call.args[0] for call in module.logger.info.call_args_list
             if call.args and isinstance(call.args[0], dict)
             and call.args[0].get("message") == "Raster header read by range"]
    assert len(lines) == 1, lines
    return lines[0]


class TestHandlerReadsByRange:
    def test_a_tiff_is_never_downloaded_and_its_header_lands_in_the_manifest(self, s3):
        data = _bytes("cog_4326")
        _seed(s3, "scene.tif", data)
        module = _load(s3)
        response = module.lambda_handler(_tiff_event("scene.tif", len(data)), None)
        assert s3.downloads == [] and s3.ranged_gets
        assert all(version == "v1" for _b, _k, _r, version in s3.ranged_gets)
        manifest = s3.manifest(_AUX, _MANIFEST_KEY)
        assert manifest["fileClass"] == "raster" and response["fileClass"] == "raster"
        assert manifest["attributes"]["sys_raster"]["isCog"] is True
        assert manifest["attributes"]["sys_geo"]["footprint"]["type"] == "Polygon"
        assert manifest["attributes"]["sys_file"]["name"] == "photo.png"  # the pre-written sys_file is kept
        log = _range_log(module)
        assert (log["rangedGets"], log["isCog"], log["layout"]) == (len(s3.ranged_gets), True, "tiled")

    def test_a_tiff_over_the_download_bound_is_read_by_range_too(self, s3, tmp_path):
        path = tmp_path / "huge.tif"
        with open(path, "wb") as handle:
            handle.write(_bytes("cog_4326"))
            handle.truncate(4 * 1024 ** 3)  # a sparse file: the header is real, the rest is a hole
        _seed(s3, "huge.tif", b"")
        del s3.objects[(_ASSETS, "a1/huge.tif")]
        s3.files[(_ASSETS, "a1/huge.tif")] = str(path)
        module = _load(s3)
        module.lambda_handler(_tiff_event("huge.tif", os.path.getsize(path)), None)
        assert os.path.getsize(path) > module.INPUT_MAX_DOWNLOAD_BYTES
        assert s3.downloads == []
        assert s3.manifest(_AUX, _MANIFEST_KEY)["attributes"]["sys_raster"]["width"] == 1024

    def test_an_unreadable_tiff_takes_the_path_it_took_before_the_raster_class(self, s3):
        _seed(s3, "scene.tif", b"not a tiff at all, just text.\n" * 20)
        module = _load(s3)
        module.lambda_handler(_tiff_event("scene.tif", 600), None)
        manifest = s3.manifest(_AUX, _MANIFEST_KEY)
        assert manifest["fileClass"] != "raster" and "sys_raster" not in manifest["attributes"]
        assert s3.downloads == [(_ASSETS, "a1/scene.tif", "v1")]  # the generic probe reads a downloaded copy

    def test_a_derived_cog_checks_its_source_with_one_head(self, s3):
        data = _bytes("cog_small_4326")
        _seed(s3, "pump.tif.cog.tif", data, extra={"a1/pump.tif": b"x"})
        heads = []
        original = s3.head_object

        def head(Bucket, Key, VersionId=None):
            heads.append((Bucket, Key))
            return original(Bucket, Key, VersionId)

        s3.head_object = head
        module = _load(s3)
        # The reader sizes itself from the state's fileSize, so the only HEAD is the source check.
        module.lambda_handler(_tiff_event("pump.tif.cog.tif", len(data)), None)
        assert heads == [(_ASSETS, "a1/pump.tif")]
        assert s3.manifest(_AUX, _MANIFEST_KEY)["attributes"]["sys_raster"]["derivedCog"] is True

    def test_a_derived_cog_name_without_its_source_is_an_original(self, s3):
        from botocore.exceptions import ClientError

        data = _bytes("cog_small_4326")
        _seed(s3, "pump.tif.cog.tif", data)

        def head(Bucket, Key, VersionId=None):
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")

        s3.head_object = head
        module = _load(s3)
        module.lambda_handler(_tiff_event("pump.tif.cog.tif", len(data)), None)
        assert "derivedCog" not in s3.manifest(_AUX, _MANIFEST_KEY)["attributes"]["sys_raster"]

    def test_a_source_check_that_fails_for_another_reason_warns_and_continues(self, s3):
        from botocore.exceptions import ClientError

        data = _bytes("cog_small_4326")
        _seed(s3, "pump.tif.cog.tif", data)

        def head(Bucket, Key, VersionId=None):
            raise ClientError({"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject")

        s3.head_object = head
        module = _load(s3)
        module.lambda_handler(_tiff_event("pump.tif.cog.tif", len(data)), None)
        manifest = s3.manifest(_AUX, _MANIFEST_KEY)
        assert "derivedCog" not in manifest["attributes"]["sys_raster"]
        assert any("could not be checked" in warning for warning in manifest["warnings"])

    def test_a_request_failure_is_re_raised_with_no_url_in_it(self, s3):
        from botocore.exceptions import ClientError

        data = _bytes("cog_small_4326")
        _seed(s3, "scene.tif", data)

        original = s3.get_object

        def get_object(Bucket, Key, Range=None, VersionId=None):
            if Range is None:
                return original(Bucket, Key, Range, VersionId)
            raise ClientError({"Error": {"Code": "AccessDenied",
                                         "Message": "https://x.s3.amazonaws.com/k?X-Amz-Signature=abc denied"}},
                              "GetObject")

        s3.get_object = get_object
        module = _load(s3)
        with pytest.raises(module.MediaTaskError) as excinfo:
            module.lambda_handler(_tiff_event("scene.tif", len(data)), None)
        assert "X-Amz-Signature" not in str(excinfo.value) and "could not be read by range" in str(excinfo.value)

    def test_the_extension_alone_routes_a_tiff_to_the_raster_extractor(self, s3):
        module = _load(s3)
        extractor, file_class = module.select_extractor(None, ".tiff")
        assert file_class == "raster" and extractor is raster.extract_raster_file
        assert module.select_extractor("raster", ".tif")[1] == "raster"

    def test_the_local_copy_path_gives_the_same_result(self, tmp_path):
        path = tmp_path / "input.tif"
        path.write_bytes(_bytes("utm33"))
        result = raster.extract_raster_file(str(path), _ctx())
        assert result.attributes["sys_raster"]["crs"] == "EPSG:32633"


# --- a TIFF that is not a georeferenced raster keeps its behaviour from before the raster class ---------------------

def _photo_tiff(width=320, height=200):
    """A plain RGB TIFF photo with an EXIF-style description, no GeoTIFF tags."""
    from PIL import Image

    out = io.BytesIO()
    image = Image.new("RGB", (width, height), (200, 120, 40))
    image.putpixel((3, 3), (1, 2, 3))
    image.save(out, format="TIFF", description="holiday scan")
    return out.getvalue()


def _run(s3, name, data, state_class, size=None):
    s3.objects.clear()
    s3.objects[(_RUN, _CONFIG_KEY)] = json.dumps(_CONFIG_BODY).encode()
    s3.puts.clear()
    s3.downloads.clear()
    s3.ranged_gets.clear()
    s3.objects[(_AUX, _MANIFEST_KEY)] = json.dumps(_pre_manifest(state_class)).encode()
    s3.objects[(_ASSETS, f"a1/{name}")] = data
    event = _event(name, state_class, "image/tiff")
    event["fileSize"] = size if size is not None else len(data)
    module = _load(s3)
    response = module.lambda_handler(event, None)
    manifest = s3.manifest(_AUX, _MANIFEST_KEY)
    renders = sorted(key for (bucket, key) in s3.objects if bucket == _AUX and key.endswith(".png"))
    return module, response, manifest, renders


@pytest.fixture
def plain_s3():
    return FakeS3()


class TestPlainTiffKeepsItsBehaviour:
    @pytest.mark.parametrize("make", [_photo_tiff, lambda: _bytes("no_georef")], ids=["photo", "plain-cog"])
    def test_a_tiff_with_no_georeference_gets_what_the_other_class_gave_it(self, plain_s3, make):
        data = make()
        # Before the raster class a .tif was classified `other` and read by the generic probe.
        _m, base_response, base, base_renders = _run(plain_s3, "scan.tif", data, "other")
        _m, response, manifest, renders = _run(plain_s3, "scan.tif", data, "raster")
        assert base["fileClass"] == "image" and "sys_image" in base["attributes"] and base_renders
        assert manifest == base
        assert renders == base_renders
        assert response["fileClass"] == base_response["fileClass"] == "image"
        assert "sys_raster" not in manifest["attributes"]

    def test_the_photo_has_the_image_extractors_facts_and_a_render(self, plain_s3):
        data = _photo_tiff()
        _m, _r, manifest, renders = _run(plain_s3, "scan.tif", data, "raster")
        sys_image = manifest["attributes"]["sys_image"]
        assert (sys_image["width"], sys_image["height"], sys_image["format"]) == (320, 200, "TIFF")
        assert manifest["renderSkipped"] is None and renders

    def test_a_georeferenced_tiff_still_takes_the_raster_path_and_is_not_downloaded(self, plain_s3):
        data = _bytes("cog_4326")
        module, _r, manifest, renders = _run(plain_s3, "scene.tif", data, "raster")
        assert manifest["fileClass"] == "raster" and "sys_image" not in manifest["attributes"]
        assert plain_s3.downloads == [] and renders == []

    def test_a_non_georeferenced_tiff_over_the_download_bound_keeps_sys_file_only_as_before(self, plain_s3, tmp_path):
        path = tmp_path / "scan.tif"
        with open(path, "wb") as handle:
            handle.write(_bytes("no_georef"))
            handle.truncate(4 * 1024 ** 3)
        plain_s3.objects.clear()
        results = {}
        for state_class in ("other", "raster"):
            plain_s3.files.clear()
            plain_s3.objects[(_RUN, _CONFIG_KEY)] = json.dumps(_CONFIG_BODY).encode()
            plain_s3.objects[(_AUX, _MANIFEST_KEY)] = json.dumps(_pre_manifest(state_class)).encode()
            plain_s3.files[(_ASSETS, "a1/scan.tif")] = str(path)
            plain_s3.downloads.clear()
            plain_s3.ranged_gets.clear()
            event = _event("scan.tif", state_class, "image/tiff")
            event["fileSize"] = os.path.getsize(path)
            _load(plain_s3).lambda_handler(event, None)
            assert plain_s3.downloads == []  # never downloaded whole
            results[state_class] = (plain_s3.manifest(_AUX, _MANIFEST_KEY), len(plain_s3.ranged_gets))
        base, base_gets = results["other"]
        manifest, gets = results["raster"]
        assert manifest == base and base_gets == 0
        assert manifest["fileClass"] == "other" and "sys_image" not in manifest["attributes"]
        assert any("exceeds" in warning for warning in manifest["warnings"])
        assert 1 <= gets <= 2  # only the header probe is new


# --- reviewer round 1: unresolvable CRS, 0..360, lazy geo tags, tifffile log noise, no-CRS transforms ---------------

DOUBLE = 12


def _directory_end(entry_count):
    return 8 + 2 + 12 * entry_count + 4


class TestUnresolvableCrs:
    def test_a_geotiff_in_a_crs_pyproj_cannot_transform_stays_a_raster_with_a_note(self):
        result = _extract(_bytes("unresolvable_crs_3145"), "faroe.tif")
        assert result.file_class == CLASS_RASTER and result.warnings == []
        facts = result.attributes["sys_raster"]
        assert (facts["width"], facts["bandCount"], facts["crs"], facts["isCog"]) == (256, 1, "EPSG:3145", True)
        assert facts["georeferenced"] is True and facts["footprintNote"] == "unknown-crs"
        assert facts["nativeBounds"] == pytest.approx([100000.0, 174400.0, 125600.0, 200000.0])
        assert "wgs84Bounds" not in facts and result.attributes["sys_geo"]["footprint"] is None
        assert result.facts["footprint"] == "none (unknown-crs)"

    def test_the_handler_keeps_the_raster_class_for_it(self, s3):
        data = _bytes("unresolvable_crs_3145")
        _seed(s3, "faroe.tif", data)
        _load(s3).lambda_handler(_tiff_event("faroe.tif", len(data)), None)
        manifest = s3.manifest(_AUX, _MANIFEST_KEY)
        assert manifest["fileClass"] == "raster" and s3.downloads == []
        assert manifest["attributes"]["sys_raster"]["footprintNote"] == "unknown-crs"


class TestGlobalRaster:
    def test_a_0_to_360_raster_has_the_whole_globe_in_its_bounds(self):
        result = _extract(_bytes("global_0to360_4326"))
        facts = result.attributes["sys_raster"]
        assert result.attributes["sys_geo"]["footprint"]["type"] == "MultiPolygon"
        assert facts["wgs84Bounds"] == [-180.0, -90.0, 180.0, 90.0] and facts["crossesAntimeridian"] is True


class TestGeoTagBounds:
    def _big_tiepoint(self, doubles):
        # ModelTiepointTag (33922) claiming `doubles` doubles, stored right after the directory.
        entries = {33922: (33922, DOUBLE, doubles, ("at", _directory_end(10)))}
        return _classic(_basic(overrides=entries), pad_to=_directory_end(10) + doubles * 8 + 64)

    def test_a_geo_tag_over_the_limit_is_refused_before_it_is_read(self):
        data = self._big_tiepoint(300_000)  # 2.4 MB of doubles, in the file, over the 256 KiB tag limit
        client = _RangeClient(data)
        reader = RangedS3Reader(client, "b", "k", size=len(data), read_ahead=4096)
        result = raster.extract_raster(reader, len(data), _ctx())
        facts = result.attributes["sys_raster"]
        assert result.file_class == CLASS_RASTER
        assert facts["offsetsTruncated"] is True and facts["footprintNote"] == "header-too-large"
        assert reader.bytes_read < 64 * 1024, reader.bytes_read  # the array itself was never fetched

    def test_a_geo_tag_within_the_limit_is_read_normally(self):
        data = self._big_tiepoint(6)
        result = _extract(data)
        assert result.file_class == CLASS_RASTER
        assert "offsetsTruncated" not in result.attributes["sys_raster"]

    def test_geo_tags_count_against_the_total_budget(self, monkeypatch):
        monkeypatch.setattr(raster, "HEADER_READ_BUDGET_BYTES", 16)
        result = _extract(self._big_tiepoint(6))
        assert result.attributes["sys_raster"]["footprintNote"] == "header-too-large"

    def test_a_ten_million_double_claim_is_refused_by_arithmetic(self):
        data = _classic(_basic(overrides={33922: (33922, DOUBLE, 10_000_000, ("at", _directory_end(10)))}),
                        pad_to=8192)
        result = _extract(data)
        assert result.file_class == CLASS_OTHER and "outside the file" in result.warnings[0]


class TestTifffileLogNoise:
    def test_a_malformed_directory_writes_nothing_to_the_tifffile_logger(self, caplog):
        import logging

        data = bytearray(_bytes("cog_small_4326"))
        data[220] ^= 0xFF  # an invalid data type in the BitsPerSample tag: tifffile logs an error line
        with caplog.at_level(logging.DEBUG, logger="tifffile"):
            result = _extract(bytes(data))
        assert result.file_class in (CLASS_OTHER, CLASS_RASTER)
        assert [record for record in caplog.records if record.name.startswith("tifffile")] == []


class TestNoCrsIsNotGeoreferenced:
    def _transform_file(self, matrix, geokeys=None):
        out = [1.0 if i in (0, 5, 15) else 0.0 for i in range(16)] if matrix is None else matrix
        start = _directory_end(10)
        entries = {34264: (34264, DOUBLE, 16, ("at", start))}
        extra = struct.pack("<16d", *out)
        if geokeys:
            entries[34735] = (34735, SHORT, len(geokeys), ("at", start + len(extra)))
            extra += struct.pack("<%dH" % len(geokeys), *geokeys)
        return _classic(_basic(overrides=entries), extra=extra, pad_to=4096)

    def test_an_identity_transform_with_no_crs_is_not_georeferenced(self):
        result = _extract(self._transform_file(None))
        facts = result.attributes["sys_raster"]
        assert facts["georeferenced"] is False and facts["crs"] == "none"
        assert facts["footprintNote"] == "not-georeferenced" and "nativeBounds" not in facts
        assert raster.is_georeferenced(result) is False

    def test_the_handler_sends_it_down_the_plain_image_path(self, plain_s3):
        data = self._transform_file(None)
        _m, _r, manifest, _renders = _run(plain_s3, "scan.tif", data, "raster")
        assert "sys_raster" not in manifest["attributes"] and manifest["fileClass"] != "raster"

    def test_a_tie_point_with_no_geokeys_is_not_georeferenced(self):
        entries = {33922: (33922, DOUBLE, 6, ("at", _directory_end(11))),
                   33550: (33550, DOUBLE, 3, ("at", _directory_end(11) + 48))}
        extra = struct.pack("<6d", 0, 0, 0, 10.0, 50.0, 0) + struct.pack("<3d", 0.1, 0.1, 0)
        result = _extract(_classic(_basic(overrides=entries), extra=extra, pad_to=4096))
        assert result.attributes["sys_raster"]["georeferenced"] is False
        assert raster.is_georeferenced(result) is False

    def test_a_real_georeferenced_file_still_counts(self):
        assert raster.is_georeferenced(_extract(_bytes("cog_4326"))) is True
        assert raster.is_georeferenced(_extract(_bytes("user_defined_laea"))) is True
