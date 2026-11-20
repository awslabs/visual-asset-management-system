# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Review round 1 regressions: D1 antimeridian, D3 SIGTERM, D4 progress log volume, D6 nodata.

Each class fails on the code of the first review (bca5e81d5). The GDAL cases skip without `gdal_translate` and
run in the image."""

import json
import os
import shutil
import subprocess
import sys
import textwrap
import time

import pytest

from cog_convert_pipeline import convert, core
from cog_convert_pipeline.tiff_header import read_header  # noqa: F401
from cog_convert_pipeline.utils.pipeline.objects import PipelineStatus
from cog_convert_pipeline.utils.aws import s3 as s3_module
from conftest import fixture_path, requires_gdal
from test_core_flow import ASSET, BUCKET, FakeS3, Tokens, _attrs, _definition, world  # noqa: F401


def _ring(*lons):
    return {"wgs84Extent": {"type": "Polygon", "coordinates": [[[lon, 0.0] for lon in lons]]}}


@pytest.mark.unit
class TestAntimeridianDetection:
    @pytest.mark.parametrize("lons", [
        (178.7460891, 178.7460891, -178.9542238, -178.9542238),  # the shared fixture's ring: 178.75E to 178.95W
        (170.0, 170.0, 190.0, 190.0),                              # the same crossing in a 0-360 convention
        (-179.9, 179.9),                                           # a thin raster straddling the line
    ])
    def test_a_raster_straddling_180_is_a_crossing(self, lons):
        assert convert.crosses_antimeridian(_ring(*lons)) is True

    @pytest.mark.parametrize("lons", [
        (15.0, 15.1),                       # an ordinary extent
        (-179.0, -170.0),                   # entirely in the western hemisphere
        (170.0, 179.9),                     # touches the east edge without crossing
        (0.0, 360.0),                       # a whole-world raster in the 0-360 convention converts fine
        (-180.0, 180.0),
    ])
    def test_everything_else_is_not(self, lons):
        assert convert.crosses_antimeridian(_ring(*lons)) is False

    def test_no_extent_is_not_a_crossing(self):
        assert convert.crosses_antimeridian({}) is False

    def test_the_refusal_names_the_remedy(self):
        with pytest.raises(convert.ConversionRefused, match="antimeridian.*Split the file"):
            convert.check_antimeridian(_ring(178.7, 178.7, -178.9, -178.9))


@requires_gdal
@pytest.mark.unit
class TestAntimeridianThroughTheFlow:
    def test_the_shared_fixture_is_detected_from_the_real_header(self):
        info = convert.inspect(fixture_path("antimeridian_3832.tif"))
        assert convert.crosses_antimeridian(info) is True

    def test_the_world_wide_raster_is_not_refused(self):
        assert convert.crosses_antimeridian(convert.inspect(fixture_path("global_0to360_4326.tif"))) is False

    def test_the_run_fails_with_the_message_and_writes_nothing(self, world):  # noqa: F811
        fake, tokens = world
        key = f"{ASSET}/antimeridian_3832.tif"
        fake.put_source(key, fixture_path("antimeridian_3832.tif"))
        out = core.run(_definition(key))
        # The first review's code converted this to a 512 x 256 world-wide COG with 9 valid pixels and
        # reported success.
        assert out.status is PipelineStatus.FAILED
        assert len(tokens.failure) == 1 and "antimeridian" in tokens.failure[0]
        assert fake.uploaded == [] and tokens.success == []


@pytest.mark.unit
class TestNodataMask:
    def test_a_source_with_nodata_and_no_alpha_goes_through_a_mask_band(self):
        info = {"bands": [{"colorInterpretation": "Gray", "noDataValue": 0}]}
        assert convert.needs_mask_band(info) is True

    @pytest.mark.parametrize("info", [
        {"bands": [{"colorInterpretation": "Gray"}]},                                           # no nodata
        {"bands": [{"colorInterpretation": "Red", "noDataValue": 0}, {"colorInterpretation": "Alpha"}]},
        {"bands": [{"colorInterpretation": "Palette", "noDataValue": 0}]},                      # indexes, not values
        {},
    ])
    def test_everything_else_is_converted_directly(self, info):
        assert convert.needs_mask_band(info) is False

    def test_the_vrt_command_keeps_the_data_bands_and_adds_the_mask_as_alpha(self):
        cmd = convert.mask_vrt_command("/w/in/a.tif", "/w/masked.vrt", 3)
        assert cmd[:4] == ["gdal_translate", "-q", "-of", "VRT"]
        assert [cmd[i + 1] for i, token in enumerate(cmd) if token == "-b"] == ["1", "2", "3", "mask"]
        assert "-a_nodata" in cmd and cmd[cmd.index("-a_nodata") + 1] == "none"
        assert cmd[cmd.index("-colorinterp_4") + 1] == "alpha"
        assert cmd[-2:] == ["/w/in/a.tif", "/w/masked.vrt"]


def _nodata_source(path):
    """A 300 x 300 UTM byte raster, nodata 0: the left third is nodata, the rest is noise of small values
    (1, 2, 3) between bright ones, so resampling produces valid pixels equal to 0."""
    import random
    random.seed(1)
    raw = path + ".raw"
    with open(raw, "wb") as handle:
        for _row in range(300):
            handle.write(bytes(0 if x < 100 else random.choice((1, 1, 2, 3, 40, 200)) for x in range(300)))
    vrt = path + ".src.vrt"
    with open(vrt, "w", encoding="utf-8") as handle:
        handle.write(
            '<VRTDataset rasterXSize="300" rasterYSize="300"><SRS>EPSG:32633</SRS>'
            '<GeoTransform>500000,10,0,5000000,0,-10</GeoTransform>'
            '<VRTRasterBand dataType="Byte" band="1" subClass="VRTRawRasterBand">'
            f'<SourceFilename relativeToVRT="1">{os.path.basename(raw)}</SourceFilename>'
            '<ImageOffset>0</ImageOffset><PixelOffset>1</PixelOffset><LineOffset>300</LineOffset>'
            '<NoDataValue>0</NoDataValue></VRTRasterBand></VRTDataset>')
    subprocess.run(["gdal_translate", "-q", "-of", "GTiff", "-co", "TILED=YES", vrt, path], check=True,
                   env=dict(os.environ, GDAL_VRT_RAWRASTERBAND_ALLOWED_SOURCE="SIBLING_OR_CHILD_OF_VRT_PATH"))


@requires_gdal
@pytest.mark.unit
class TestNodataThroughGdal:
    def test_valid_pixels_are_not_rewritten_and_nodata_becomes_transparent(self, tmp_path):
        source = str(tmp_path / "nd.tif")
        _nodata_source(source)
        info = convert.inspect(source)
        assert convert.needs_mask_band(info)
        vrt = convert.prepare_source(source, str(tmp_path), info, 1 << 30)
        assert vrt.endswith("masked.vrt")

        output = str(tmp_path / "nd.tif.cog.tif")
        process = subprocess.run(
            convert.build_command(vrt, output, {"compression": "DEFLATE"}, 2), capture_output=True,
            env=convert.gdal_env(1 << 30), check=True)
        # The first review's code left "Value 0 in the source dataset has been changed to 1" on stderr.
        assert b"changed to 1" not in process.stdout + process.stderr
        out = json.loads(subprocess.run(["gdalinfo", "-json", "-stats", output], capture_output=True, check=True,
                                        env=convert.gdal_env(1 << 30)).stdout)
        bands = out["bands"]
        assert [b["colorInterpretation"] for b in bands] == ["Gray", "Alpha"]
        assert "noDataValue" not in bands[0]
        assert float(bands[1]["minimum"]) == 0.0 and float(bands[1]["maximum"]) == 255.0  # transparent and opaque

    def test_the_previous_direct_conversion_does_reproduce_the_rewrite(self, tmp_path):
        # The control for the test above: without the mask band GDAL does rewrite the pixels.
        source = str(tmp_path / "nd.tif")
        _nodata_source(source)
        output = str(tmp_path / "direct.tif")
        process = subprocess.run(
            convert.build_command(source, output, {"compression": "DEFLATE"}, 2), capture_output=True,
            env=convert.gdal_env(1 << 30), check=True)
        assert b"changed to 1" in process.stdout + process.stderr


@pytest.mark.unit
class TestUploadProgressLog:
    def test_a_large_upload_logs_each_quarter_once(self, tmp_path, caplog):
        path = tmp_path / "big.bin"
        path.write_bytes(b"x" * 1000)
        progress = s3_module.ProgressPercentage(str(path))
        with caplog.at_level("INFO"):
            for _chunk in range(1000):  # a callback per byte-sized chunk
                progress(1)
        lines = [r.getMessage() for r in caplog.records if "Upload progress" in r.getMessage()]
        # The first review's code logged on every callback inside a 25% band (207 lines for one 1.3 GB upload).
        assert [line.rsplit(" ", 1)[1] for line in lines] == ["25%", "50%", "75%", "100%"]

    def test_an_empty_file_does_not_divide_by_zero(self, tmp_path):
        path = tmp_path / "empty.bin"
        path.write_bytes(b"")
        s3_module.ProgressPercentage(str(path))(0)


_SIGTERM_CHILD = textwrap.dedent('''
    import os, sys, time
    sys.path.insert(0, {container!r})
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
    from cog_convert_pipeline import __main__ as entry, convert
    from cog_convert_pipeline.utils.aws import sfn

    def record(message="", name=""):
        with open({marker!r}, "w") as handle:
            handle.write(message)
    sfn.send_task_failure = record
    entry.install_sigterm_handler()
    # a long GDAL-like child under run_streaming
    print("ready", flush=True)
    convert.run_streaming([sys.executable, "-c", "import time; time.sleep(120)"])
    time.sleep(120)
''')


@pytest.mark.unit
class TestSigterm:
    def test_sigterm_reports_the_failure_stops_the_child_and_exits_within_the_grace_period(self, tmp_path):
        container = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        marker = str(tmp_path / "reported.txt")
        script = tmp_path / "child.py"
        script.write_text(_SIGTERM_CHILD.format(container=container, marker=marker), encoding="utf-8")
        process = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            assert process.stdout.readline().strip() == b"ready"
            time.sleep(1.0)  # let it reach the child process
            started = time.time()
            process.terminate()
            code = process.wait(timeout=25)
            elapsed = time.time() - started
        finally:
            if process.poll() is None:
                process.kill()
        # The first review's image ignored SIGTERM (Python is PID 1) and was killed after the grace period.
        assert code == 143 and elapsed < 25
        assert "SIGTERM" in open(marker, encoding="utf-8").read()

    def test_the_handler_is_installed_by_main(self, monkeypatch):
        import signal
        from cog_convert_pipeline import __main__ as entry
        previous = signal.getsignal(signal.SIGTERM)
        monkeypatch.setattr(entry.sys, "argv", ["prog", "{not json"])
        monkeypatch.setattr(core.sfn, "send_task_failure", lambda msg="", name="": None)
        try:
            entry.main()
            assert signal.getsignal(signal.SIGTERM) is not previous
        finally:
            signal.signal(signal.SIGTERM, previous)
