# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The whole stage against a fake S3 and the real GDAL command line.

Covers the conversion of a UTM and a 4326 file into a Web Mercator COG, the skip rules, the output name and
folder, the link attributes, the task-token reporting on every failure route, and that the original is never
written. The GDAL cases skip when `gdal_translate` is not on PATH; the image carries it."""

import json
import shutil

import pytest

from cog_convert_pipeline import core, convert
from cog_convert_pipeline.tiff_header import read_header
from cog_convert_pipeline.utils.pipeline.objects import PipelineStatus
from conftest import fixture_path, requires_gdal

BUCKET = "assets"
ASSET = "xA1"


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.versions = {}
        self.uploaded = []

    def put_source(self, key, path, version="v-1"):
        with open(path, "rb") as handle:
            self.objects[(BUCKET, key)] = handle.read()
        self.versions[(BUCKET, key)] = version

    def head(self, bucket, key, version_id=""):
        body = self.objects.get((bucket, key))
        if body is None:
            return None
        return {"ContentLength": len(body), "VersionId": self.versions.get((bucket, key), "")}

    def download(self, bucket, key, path, version_id=""):
        with open(path, "wb") as handle:
            handle.write(self.objects[(bucket, key)])
        return path

    def upload(self, bucket, key, path):
        with open(path, "rb") as handle:
            self.objects[(bucket, key)] = handle.read()
        self.uploaded.append(key)
        return key

    def put_text(self, bucket, key, text):
        self.objects[(bucket, key)] = text.encode()
        return key


class Tokens:
    def __init__(self):
        self.success, self.failure = [], []


@pytest.fixture
def world(monkeypatch):
    fake, tokens = FakeS3(), Tokens()
    for name in ("head", "download", "upload", "put_text"):
        monkeypatch.setattr(core.s3, name, getattr(fake, name))
    monkeypatch.setattr(core.s3, "register_bucket_regions", lambda _m: None)
    monkeypatch.setattr(core.sfn, "send_task_heartbeat", lambda _t: None)
    monkeypatch.setattr(core.sfn, "send_task_success", lambda out: tokens.success.append(out))
    monkeypatch.setattr(core.sfn, "send_task_failure", lambda msg="", name="": tokens.failure.append(msg))
    return fake, tokens


def _definition(key, version=""):
    return {
        "jobName": "job1",
        "stages": [{
            "type": "COG_CONVERT",
            "inputFile": {"bucketName": BUCKET, "objectKey": key, "fileExtension": ".tif", "versionId": version},
            "outputFiles": {"bucketName": BUCKET, "objectDir": "pipelines/p/files"},
            "outputMetadata": {"bucketName": BUCKET, "objectDir": "pipelines/p/metadata"},
            "outputResults": {"bucketName": BUCKET, "objectDir": "pipelines/p/results"},
        }],
        "inputMetadata": "", "inputParameters": json.dumps({"compression": "DEFLATE"}),
        "externalSfnTaskToken": "ext", "assetId": ASSET, "databaseId": "db", "bucketRegions": {},
    }


def _attrs(fake, key):
    return {r["metadataKey"]: r["metadataValue"]
            for r in json.loads(fake.objects[(BUCKET, key)])["metadata"]}


def _write_cog(fake, key, tmp_path):
    path = tmp_path / "out.tif"
    path.write_bytes(fake.objects[(BUCKET, key)])
    return read_header(str(path))


@requires_gdal
@pytest.mark.unit
@pytest.mark.parametrize("fixture,source_crs", [
    ("utm33.tif", "EPSG:32633"),
    ("striped_4326.tif", "EPSG:4326"),
    ("cog_4326.tif", "EPSG:4326"),       # already a COG, but in the wrong CRS for the map: converted
    ("rotated_utm33.tif", "EPSG:32633"),
])
def test_a_file_in_another_crs_becomes_a_web_mercator_cog_beside_it(world, tmp_path, fixture, source_crs):
    fake, tokens = world
    key = f"{ASSET}/maps/north/{fixture}"
    fake.put_source(key, fixture_path(fixture))
    original = bytes(fake.objects[(BUCKET, key)])

    out = core.run(_definition(key, version="v-1"))

    assert out.status is PipelineStatus.COMPLETE and tokens.failure == [] and len(tokens.success) == 1
    cog_key = f"pipelines/p/files/maps/north/{fixture}.cog.tif"
    assert fake.uploaded == [cog_key]
    header = _write_cog(fake, cog_key, tmp_path)
    assert header.is_cog and header.is_web_mercator and header.georeferenced
    assert header.compression == "deflate"
    assert fake.objects[(BUCKET, key)] == original  # the original is never written
    cog_rows = _attrs(fake, f"pipelines/p/metadata/maps/north/{fixture}.cog.tif.attribute.json")
    assert cog_rows["cog_link_cogOf"] == f"/maps/north/{fixture}"
    assert cog_rows["cog_link_cogOfVersionId"] == "v-1"
    assert cog_rows["cog_link_sourceCrs"] == source_crs and cog_rows["cog_link_outputCrs"] == "EPSG:3857"
    assert cog_rows["cog_link_cogCompression"] == "deflate"
    assert _attrs(fake, f"pipelines/p/metadata/maps/north/{fixture}.attribute.json") == {
        "cog_link_cogFile": f"/maps/north/{fixture}.cog.tif"}


@requires_gdal
@pytest.mark.unit
def test_the_converted_file_passes_gdals_own_cog_layout_check(world, tmp_path):
    fake, _tokens = world
    key = f"{ASSET}/utm33.tif"
    fake.put_source(key, fixture_path("utm33.tif"))
    core.run(_definition(key))
    path = tmp_path / "c.tif"
    path.write_bytes(fake.objects[(BUCKET, "pipelines/p/files/utm33.tif.cog.tif")])
    code, tail = convert.run_streaming(["gdalinfo", "-json", str(path)], env=convert.gdal_env(1 << 30), tail_lines=400)
    assert code == 0
    info = json.loads(tail) if tail.lstrip().startswith("{") else {}
    assert info.get("metadata", {}).get("IMAGE_STRUCTURE", {}).get("LAYOUT") == "COG"


@requires_gdal
@pytest.mark.unit
def test_tif_and_tiff_in_one_folder_get_two_outputs(world):
    fake, _tokens = world
    fake.put_source(f"{ASSET}/a.tif", fixture_path("utm33.tif"))
    fake.put_source(f"{ASSET}/a.tiff", fixture_path("striped_4326.tif"))
    core.run(_definition(f"{ASSET}/a.tif"))
    core.run(_definition(f"{ASSET}/a.tiff"))
    assert sorted(fake.uploaded) == ["pipelines/p/files/a.tif.cog.tif", "pipelines/p/files/a.tiff.cog.tif"]


@pytest.mark.unit
@pytest.mark.parametrize("fixture,why", [
    ("epsg3857.tif", "already a Cloud Optimized GeoTIFF in EPSG:3857"),
    ("no_georef.tif", "not georeferenced"),
])
def test_a_file_with_nothing_to_convert_exits_clean_with_a_note(world, fixture, why):
    fake, tokens = world
    key = f"{ASSET}/{fixture}"
    fake.put_source(key, fixture_path(fixture))
    out = core.run(_definition(key))
    assert out.status is PipelineStatus.COMPLETE and len(tokens.success) == 1 and tokens.failure == []
    assert fake.uploaded == []
    note = json.loads(fake.objects[(BUCKET, "pipelines/p/results/cog-convert-skipped.json")])
    assert note["outcome"] == "skipped" and why in note["reason"]


@pytest.mark.unit
def test_a_corrupt_file_fails_and_reports_the_token(world):
    fake, tokens = world
    key = f"{ASSET}/bad.tif"
    fake.objects[(BUCKET, key)] = b"this is not a tiff file at all"
    out = core.run(_definition(key))
    assert out.status is PipelineStatus.FAILED
    assert len(tokens.failure) == 1 and "not a TIFF" in tokens.failure[0] and tokens.success == []


@pytest.mark.unit
def test_a_pixel_limit_refusal_fails_with_the_limit(world, monkeypatch):
    fake, tokens = world
    key = f"{ASSET}/utm33.tif"
    fake.put_source(key, fixture_path("utm33.tif"))
    monkeypatch.setattr(convert, "MAX_RAW_BYTES", 10)
    out = core.run(_definition(key))
    assert out.status is PipelineStatus.FAILED and "uncompressed" in tokens.failure[0]
    assert fake.uploaded == []


@pytest.mark.unit
def test_a_missing_input_fails_and_reports_the_token(world):
    _fake, tokens = world
    out = core.run(_definition(f"{ASSET}/gone.tif"))
    assert out.status is PipelineStatus.FAILED and len(tokens.failure) == 1


@pytest.mark.unit
def test_a_gdal_failure_reports_the_tail_in_the_task_failure(world, monkeypatch):
    fake, tokens = world
    key = f"{ASSET}/utm33.tif"
    fake.put_source(key, fixture_path("utm33.tif"))
    monkeypatch.setattr(convert, "run_streaming", lambda *a, **k: (1, "ERROR 1: warp exploded"))
    out = core.run(_definition(key))
    assert out.status is PipelineStatus.FAILED and "warp exploded" in tokens.failure[0]


@pytest.mark.unit
def test_an_upload_failure_fails_the_run(world, monkeypatch):
    fake, tokens = world
    key = f"{ASSET}/utm33.tif"
    fake.put_source(key, fixture_path("utm33.tif"))
    monkeypatch.setattr(convert, "convert", lambda i, o, p: shutil.copy(fixture_path("epsg3857.tif"), o))
    monkeypatch.setattr(core.s3, "upload", lambda *a, **k: None)
    out = core.run(_definition(key))
    assert out.status is PipelineStatus.FAILED and "upload" in tokens.failure[0].lower()


@pytest.mark.unit
def test_attribute_write_failure_after_the_cog_exists_is_reported_through_the_results_channel(world, monkeypatch):
    fake, tokens = world
    key = f"{ASSET}/utm33.tif"
    fake.put_source(key, fixture_path("utm33.tif"))
    monkeypatch.setattr(convert, "convert", lambda i, o, p: shutil.copy(fixture_path("epsg3857.tif"), o))
    real_put = fake.put_text

    def put(bucket, k, text):
        return None if "metadata" in k else real_put(bucket, k, text)

    monkeypatch.setattr(core.s3, "put_text", put)
    out = core.run(_definition(key))
    assert out.status is PipelineStatus.COMPLETE and tokens.failure == []
    status = json.loads(fake.objects[(BUCKET, "pipelines/p/results/execution.status.json")])
    assert status["status"] == "FAILED" and status["error"] == "CogLinkAttributesNotWritten"
    assert "pipelines/p/files/utm33.tif.cog.tif" in fake.uploaded


@pytest.mark.unit
def test_a_rejected_success_report_ends_the_task_with_a_failure(world, monkeypatch):
    fake, tokens = world
    key = f"{ASSET}/epsg3857.tif"
    fake.put_source(key, fixture_path("epsg3857.tif"))

    def refuse(_out):
        raise RuntimeError("token expired")

    monkeypatch.setattr(core.sfn, "send_task_success", refuse)
    out = core.run(_definition(key))
    assert out.status is PipelineStatus.FAILED and "token expired" in tokens.failure[0]


@pytest.mark.unit
def test_main_reports_an_unexpected_error_on_the_task_token(monkeypatch):
    from cog_convert_pipeline import __main__ as entry
    from cog_convert_pipeline.utils.aws import sfn

    import signal

    sent = []
    previous = signal.getsignal(signal.SIGTERM)
    monkeypatch.setattr(sfn, "send_task_failure", lambda msg="", name="": sent.append(msg))
    monkeypatch.setattr(entry.sys, "argv", ["prog", "{not json"])
    try:
        assert entry.main() == 1
    finally:
        signal.signal(signal.SIGTERM, previous)
    assert len(sent) == 1
