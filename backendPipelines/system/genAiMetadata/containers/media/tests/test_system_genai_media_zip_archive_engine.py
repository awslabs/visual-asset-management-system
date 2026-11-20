#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""An archive the Zip Archive pipeline wrote into an asset is listed like any other archive.

A Zip Archive workflow run writes its archive into the asset, and the SYSTEM GenAI metadata trigger then
analyses it (trigger chaining). The archive is produced by the Zip Archive pipeline's own streaming engine
(`backendPipelines/utility/zipArchive/container/zip_archive_pipeline/engine/`), whose output differs from
`zipfile`'s: every member carries a data descriptor (bit 3, sizes zero in the local header), directories are
explicit entries (a listing counts files only, as for any zip), names may be UTF-8, and past 65,535 entries it
writes the ZIP64 end records. The media branch lists such a ZIP by ranged reads of its end records and central
directory, never downloading it, and lists the engine's pax tar and tar.gz from the downloaded copy, as D109
bounds both.

The engine modules are loaded by file path (they are stdlib only), as submodules of one package so the
engine's own relative imports resolve (`tarstream` imports `check_member_name` from `zipstream`); this suite
does not run the Zip Archive container.
"""

import importlib
import importlib.util
import io
import json
import os
import sys

import pytest

from system_genai_media_fixtures import FakeS3
from test_system_genai_media_handler import (
    _ASSETS, _AUX, _CONFIG_BODY, _CONFIG_KEY, _MANIFEST_KEY, _RUN, _event, _load, _pre_manifest,
)

_ENGINE_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), *[os.pardir] * 5, "utility", "zipArchive", "container", "zip_archive_pipeline",
    "engine"))


_ENGINE_PACKAGE = "zip_archive_engine"


def _engine(name):
    """The engine module ``name``, imported as ``zip_archive_engine.<name>`` from the engine directory."""
    if _ENGINE_PACKAGE not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            _ENGINE_PACKAGE, os.path.join(_ENGINE_DIR, "__init__.py"),
            submodule_search_locations=[_ENGINE_DIR])
        package = importlib.util.module_from_spec(spec)
        sys.modules[_ENGINE_PACKAGE] = package
        spec.loader.exec_module(package)
    return importlib.import_module(f"{_ENGINE_PACKAGE}.{name}")


@pytest.fixture
def s3():
    fake = FakeS3()
    fake.objects[(_RUN, _CONFIG_KEY)] = json.dumps(_CONFIG_BODY).encode()
    return fake


def _seed(s3, detected_format):
    manifest = _pre_manifest("other")
    manifest["attributes"]["sys_file"]["detectedFormat"] = detected_format
    s3.objects[(_AUX, _MANIFEST_KEY)] = json.dumps(manifest).encode()


def _archive_event(name, size, content_type):
    event = _event(name, "other", content_type)
    event["fileSize"] = size
    return event


def _zip(members, directories=(), compression="store"):
    out = io.BytesIO()
    writer = _engine("zipstream").ZipStreamWriter(out, compression=compression)
    for name in directories:
        writer.add_directory(name)
    for name, data in members:
        writer.add_file(name, [data], len(data))
    writer.close()
    return out.getvalue()


@pytest.mark.unit
class TestZipFromTheZipArchiveEngine:
    @pytest.mark.parametrize("compression", ["store", "deflate"])
    def test_its_zip_is_listed_by_range(self, s3, compression):
        data = _zip([("scans/line-3/a.las", b"L" * 4000), ("docs/Prüfbericht.pdf", b"%PDF" + b"x" * 900),
                     ("model.glb", b"glTF" + b"\x00" * 500)], directories=["empty/"], compression=compression)
        s3.objects[(_ASSETS, "a1/a1-archive.zip")] = data
        _seed(s3, "zip")
        module = _load(s3)
        module.lambda_handler(_archive_event("a1-archive.zip", len(data), "application/zip"), None)
        assert s3.downloads == [] and s3.ranged_gets
        archive = s3.manifest(_AUX, _MANIFEST_KEY)["attributes"]["sys_archive"]
        # Directory entries are not counted, as for any zip.
        assert archive["containerFormat"] == "zip" and archive["entryCount"] == 3
        assert set(archive["entries"]) == {"scans/line-3/a.las", "docs/Prüfbericht.pdf", "model.glb"}
        assert archive["uncompressedBytes"] == 4000 + 904 + 504
        assert archive["topLevelEntries"] == ["docs", "model.glb", "scans"]
        assert "listing" not in archive and "partial" not in archive

    def test_its_zip64_end_records_past_65535_entries_are_read(self, s3):
        count = 70_000
        out = io.BytesIO()
        writer = _engine("zipstream").ZipStreamWriter(out)
        for index in range(count):
            writer.add_file(f"tiles/{index:05d}.png", [b"P"], 1)
        writer.close()
        data = out.getvalue()
        s3.objects[(_ASSETS, "a1/tiles.zip")] = data
        _seed(s3, "zip")
        module = _load(s3)
        module.lambda_handler(_archive_event("tiles.zip", len(data), "application/zip"), None)
        assert s3.downloads == []
        archive = s3.manifest(_AUX, _MANIFEST_KEY)["attributes"]["sys_archive"]
        assert archive["entryCount"] == count and archive["entryExtensions"] == {".png": count}
        assert len(archive["entries"]) == 50 and archive["topLevelEntries"] == ["tiles"]


@pytest.mark.unit
class TestTarFromTheZipArchiveEngine:
    @pytest.mark.parametrize("gzip_on, name, content_type", [
        (False, "a1-archive.tar", "application/x-tar"),
        (True, "a1-archive.tar.gz", "application/gzip"),
    ])
    def test_its_tar_is_listed_from_the_downloaded_copy(self, s3, gzip_on, name, content_type):
        out = io.BytesIO()
        writer = _engine("tarstream").TarStreamWriter(out, gzip=gzip_on)
        writer.add_directory("empty/")
        writer.add_file("scans/a.las", [b"L" * 3000], 3000)
        writer.add_file("docs/Prüfbericht.pdf", [b"%PDF"], 4)
        writer.close()
        data = out.getvalue()
        s3.objects[(_ASSETS, f"a1/{name}")] = data
        _seed(s3, "gzip" if gzip_on else "tar")
        module = _load(s3)
        module.lambda_handler(_archive_event(name, len(data), content_type), None)
        archive = s3.manifest(_AUX, _MANIFEST_KEY)["attributes"]["sys_archive"]
        assert archive["containerFormat"] == "tar"
        assert {"scans/a.las", "docs/Prüfbericht.pdf"} <= set(archive["entries"])
        assert archive["uncompressedBytes"] == 3004 and "partial" not in archive
