#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Archives of any size: a ZIP container is listed from its central directory by ranged reads, a tar scan is
bounded (D109).

A ZIP over the MEDIA download bound got sys_file only: the image downloaded the whole input before the generic
probe read the archive's central directory, which is all a listing needs. A ZIP container (an `other` file whose
signature constructPipeline's sniff identified as zip, ZIP64 included) is now listed through a seekable ranged-read
file object over the S3 object at the run's version: the end-of-central-directory record from the file's tail, then
the central directory, and nothing else but two bounded OPC docProps parts. A central directory over the cap is
not parsed; the record's entry count and the directory's size stand with `listing: "skipped"`. A tar is still read
from a downloaded copy, but its header scan stops at a member or time bound and marks the listing `partial`.

The large archives here are sparse files: their member data are holes the file system does not store, so a 6 GiB
ZIP64 costs a few hundred kilobytes of disk, and the stub S3 serves its bytes by range from the local file and
refuses a whole-object read or download.
"""

import importlib
import io
import json
import os
import struct
import tarfile
import zipfile

import pytest

from media_extractors import generic
from media_extractors.common import ExtractContext
from system_genai_media_fixtures import FakeS3
from test_system_genai_media_handler import (
    _ASSETS, _AUX, _CONFIG_BODY, _CONFIG_KEY, _MANIFEST_KEY, _RUN, _event, _load, _pre_manifest,
)

GIB = 1024 ** 3


def _zip64_sparse(path, entries):
    """Write a ZIP64 archive of STORED `entries` ((name, size) pairs) whose member data are holes: each local
    header is written and the file pointer skips the member's size. The central directory, the ZIP64
    end-of-central-directory record and locator, and the classic record (saturated) follow. CRCs are zero, which a
    listing never checks."""
    offsets = []
    with open(path, "wb") as handle:
        for name, size in entries:
            encoded = name.encode()
            offsets.append(handle.tell())
            extra = struct.pack("<HHQQ", 0x0001, 16, size, size)
            handle.write(struct.pack("<4s5H3L2H", b"PK\x03\x04", 45, 0, 0, 0, 0x21, 0, 0xFFFFFFFF, 0xFFFFFFFF,
                                     len(encoded), len(extra)) + encoded + extra)
            handle.seek(size, os.SEEK_CUR)
        cd_offset = handle.tell()
        for (name, size), offset in zip(entries, offsets):
            encoded = name.encode()
            extra = struct.pack("<HHQQQ", 0x0001, 24, size, size, offset)
            handle.write(struct.pack("<4s6H3L5H2L", b"PK\x01\x02", 45, 45, 0, 0, 0, 0x21, 0, 0xFFFFFFFF, 0xFFFFFFFF,
                                     len(encoded), len(extra), 0, 0, 0, 0, 0xFFFFFFFF) + encoded + extra)
        cd_size = handle.tell() - cd_offset
        record_offset = handle.tell()
        handle.write(struct.pack("<4sQ2H2L4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, len(entries), len(entries),
                                 cd_size, cd_offset))
        handle.write(struct.pack("<4sLQL", b"PK\x06\x07", 0, record_offset, 1))
        handle.write(struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0))
    return cd_size


def _directory_only_sparse(path, entries, cd_size, data_size):
    """An archive whose end records claim `entries` records in a `cd_size`-byte central directory after
    `data_size` bytes of members, all of it a hole: only the records at the tail exist on disk."""
    with open(path, "wb") as handle:
        handle.write(b"PK\x03\x04")
        handle.seek(data_size + cd_size)
        record_offset = handle.tell()
        handle.write(struct.pack("<4sQ2H2L4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, entries, entries, cd_size,
                                 data_size))
        handle.write(struct.pack("<4sLQL", b"PK\x06\x07", 0, record_offset, 1))
        handle.write(struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0))


_LARGE_ENTRIES = ([(f"data/part-{index:02d}.bin", 2 * GIB) for index in range(3)]
                  + [(f"assets/img-{index:04d}.png", 0) for index in range(1500)])


@pytest.fixture
def s3():
    fake = FakeS3()
    fake.objects[(_RUN, _CONFIG_KEY)] = json.dumps(_CONFIG_BODY).encode()
    return fake


def _seed_other(s3, detected_format):
    manifest = _pre_manifest("other")
    manifest["attributes"]["sys_file"]["detectedFormat"] = detected_format
    s3.objects[(_AUX, _MANIFEST_KEY)] = json.dumps(manifest).encode()


def _zip_event(name, size):
    event = _event(name, "other", "application/zip")
    event["fileSize"] = size
    return event


def _range_log(module):
    lines = [call.args[0] for call in module.logger.info.call_args_list
             if call.args and isinstance(call.args[0], dict)
             and call.args[0].get("message") == "ZIP container read by range"]
    assert len(lines) == 1, lines
    return lines[0]


def _bytes_read(s3, key):
    total = 0
    for _bucket, read_key, byte_range, _version in s3.ranged_gets:
        if read_key == key:
            first, _, last = byte_range[len("bytes="):].partition("-")
            total += int(last) - int(first) + 1
    return total


@pytest.mark.unit
class TestZipListedByRange:
    def test_a_zip_over_the_download_bound_is_listed_by_range_without_a_download(self, s3, tmp_path):
        path = tmp_path / "survey-photos.zip"
        cd_size = _zip64_sparse(path, _LARGE_ENTRIES)
        size = os.path.getsize(path)
        s3.files[(_ASSETS, "a1/survey-photos.zip")] = str(path)
        _seed_other(s3, "zip")
        module = _load(s3)
        assert size > module.INPUT_MAX_DOWNLOAD_BYTES

        response = module.lambda_handler(_zip_event("survey-photos.zip", size), None)

        assert s3.downloads == []
        assert all(version == "v1" for _b, _k, _r, version in s3.ranged_gets)
        read = _bytes_read(s3, "a1/survey-photos.zip")
        # The tail and the central directory, read in at most two GETs: no member data.
        assert len(s3.ranged_gets) <= 2 and read <= 128 * 1024 + cd_size + 1024
        log = _range_log(module)
        assert (log["rangedGets"], log["bytesRead"], log["fileSize"]) == (len(s3.ranged_gets), read, size)
        manifest = s3.manifest(_AUX, _MANIFEST_KEY)
        archive = manifest["attributes"]["sys_archive"]
        assert archive["containerFormat"] == "zip" and archive["entryCount"] == 1503
        assert archive["uncompressedBytes"] == 6 * GIB
        assert archive["entries"][:3] == ["data/part-00.bin", "data/part-01.bin", "data/part-02.bin"]
        assert len(archive["entries"]) == 50 and archive["topLevelEntries"] == ["assets", "data"]
        assert archive["entryExtensions"] == {".png": 1500, ".bin": 3}
        assert "listing" not in archive and "partial" not in archive
        assert manifest["fileClass"] == "other" and response["renderSkipped"] is None
        assert manifest["facts"]["entries"] == "1,503 entries"

    def test_the_zip64_end_record_agrees_with_zipfile(self, tmp_path):
        path = tmp_path / "big.zip"
        cd_size = _zip64_sparse(path, _LARGE_ENTRIES)
        with open(path, "rb") as handle:
            end = generic.read_zip_end_record(handle, os.path.getsize(path))
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
        assert end["entryCount"] == len(infos) == 1503 and end["centralDirectoryBytes"] == cd_size
        assert end["centralDirectoryOffset"] == 3 * 2 * GIB + sum(30 + len(n) + 20 for n, _s in _LARGE_ENTRIES)
        assert sum(info.file_size for info in infos) == 6 * GIB

    def test_a_central_directory_over_the_cap_is_counted_not_listed(self, s3, tmp_path):
        path = tmp_path / "telemetry.zip"
        _directory_only_sparse(path, entries=2_000_000, cd_size=100 * 1024 * 1024, data_size=8 * GIB)
        s3.files[(_ASSETS, "a1/telemetry.zip")] = str(path)
        _seed_other(s3, "zip")
        module = _load(s3)
        module.lambda_handler(_zip_event("telemetry.zip", os.path.getsize(path)), None)
        assert s3.downloads == []
        assert len(s3.ranged_gets) == 1 and _bytes_read(s3, "a1/telemetry.zip") == 128 * 1024
        archive = s3.manifest(_AUX, _MANIFEST_KEY)["attributes"]["sys_archive"]
        assert archive["entryCount"] == 2_000_000 and archive["centralDirectoryBytes"] == 100 * 1024 * 1024
        assert archive["listing"] == "skipped" and "over the" in archive["listingNote"]
        assert "entries" not in archive and archive["containerFormat"] == "zip"
        assert _range_log(module)["listing"] == "skipped"

    def test_a_small_zip_is_read_by_range_too_with_its_opc_properties(self, s3):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("docProps/core.xml",
                             '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
                             'metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">'
                             "<dc:title>Line 3 survey</dc:title><dc:creator>QA</dc:creator></cp:coreProperties>")
            archive.writestr("scans/a.txt", "x" * 5000)
        data = out.getvalue()
        s3.objects[(_ASSETS, "a1/pack.zip")] = data
        _seed_other(s3, "zip")
        module = _load(s3)
        module.lambda_handler(_zip_event("pack.zip", len(data)), None)
        assert s3.downloads == [] and s3.ranged_gets
        archive = s3.manifest(_AUX, _MANIFEST_KEY)["attributes"]["sys_archive"]
        assert archive["coreProperties"] == {"title": "Line 3 survey", "creator": "QA"}
        assert archive["entryCount"] == 2

    def test_a_truncated_zip_falls_back_as_today(self, s3):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as archive:
            archive.writestr("big.bin", bytes(range(256)) * 400)
        data = out.getvalue()[:60_000]  # cut before the central directory
        s3.objects[(_ASSETS, "a1/cut.zip")] = data
        _seed_other(s3, "zip")
        module = _load(s3)
        module.lambda_handler(_zip_event("cut.zip", len(data)), None)
        # No end record by range, so the file is downloaded and probed as before: no probe recognises it.
        assert s3.downloads == [(_ASSETS, "a1/cut.zip", "v1")]
        manifest = s3.manifest(_AUX, _MANIFEST_KEY)
        assert manifest["fileClass"] == "other" and "sys_archive" not in manifest["attributes"]
        assert any("no probe recognised the format" in warning for warning in manifest["warnings"])

    def test_a_zip_named_file_that_is_not_a_zip_takes_the_usual_path(self, s3):
        s3.objects[(_ASSETS, "a1/readme.zip")] = b"This is a note saved with the wrong extension.\n"
        _seed_other(s3, "utf8-text")
        module = _load(s3)
        module.lambda_handler(_zip_event("readme.zip", 48), None)
        assert s3.ranged_gets == [] and s3.downloads == [(_ASSETS, "a1/readme.zip", "v1")]
        assert s3.manifest(_AUX, _MANIFEST_KEY)["fileClass"] == "text"


@pytest.mark.unit
class TestRangedReader:
    def _reader(self, data, **kwargs):
        reader_module = importlib.import_module("media_extractors.s3_ranged")
        s3 = FakeS3()
        s3.objects[("b", "k")] = data
        return reader_module, s3, reader_module.RangedS3Reader(s3, "b", "k", "v9", size=len(data), **kwargs)

    def test_reads_seeks_and_serves_repeat_reads_from_the_fetched_block(self):
        _module, s3, reader = self._reader(bytes(range(256)) * 16, read_ahead=1024)
        reader.seek(-16, io.SEEK_END)
        assert reader.read() == bytes(range(240, 256))
        reader.seek(100)
        assert reader.read(4) == bytes([100, 101, 102, 103]) and reader.tell() == 104
        assert reader.read(4) == bytes([104, 105, 106, 107])
        assert [r for _b, _k, r, _v in s3.ranged_gets] == ["bytes=4080-4095", "bytes=100-1123"]
        assert all(version == "v9" for _b, _k, _r, version in s3.ranged_gets)
        assert (reader.gets, reader.bytes_read) == (2, 16 + 1024)

    @pytest.mark.parametrize("bound", [{"max_gets": 2}, {"max_bytes": 2048}])
    def test_a_read_past_the_bound_raises(self, bound):
        module, _s3, reader = self._reader(b"\x00" * 10_000, read_ahead=1024, **bound)
        reader.read(1024)
        reader.read(1024)
        with pytest.raises(module.RangedReadBudgetExceeded):
            reader.read(1024)


def _tar(path, count, compression=""):
    with tarfile.open(path, "w" + (":" + compression if compression else "")) as archive:
        for index in range(count):
            payload = f"member {index}\n".encode()
            info = tarfile.TarInfo(f"logs/run-{index:03d}.log")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))


def _ctx(tmp_path, name):
    return ExtractContext(file_name=name, extension=os.path.splitext(name)[1], content_type="application/x-tar",
                          max_text_chars=1000, work_dir=str(tmp_path))


@pytest.mark.unit
class TestTarScanBound:
    @pytest.mark.parametrize("compression", ["", "gz"])
    def test_a_tar_past_the_member_bound_is_partial(self, tmp_path, monkeypatch, compression):
        monkeypatch.setattr(generic, "TAR_SCAN_MAX_MEMBERS", 10)
        path = tmp_path / "logs.tar"
        _tar(path, 25, compression)
        result = generic.extract_generic(str(path), _ctx(tmp_path, "logs.tar"))
        archive = result.attributes["sys_archive"]
        assert archive["containerFormat"] == "tar" and archive["partial"] is True
        assert archive["entryCount"] == 10 and archive["uncompressedBytes"] == sum(
            len(f"member {index}\n") for index in range(10))
        assert "10-member bound" in archive["listingNote"] and result.facts["entries"] == "10 entries (partial)"

    def test_a_tar_at_the_member_bound_is_complete(self, tmp_path, monkeypatch):
        monkeypatch.setattr(generic, "TAR_SCAN_MAX_MEMBERS", 10)
        path = tmp_path / "logs.tar"
        _tar(path, 10)
        archive = generic.extract_generic(str(path), _ctx(tmp_path, "logs.tar")).attributes["sys_archive"]
        assert archive["entryCount"] == 10 and "partial" not in archive

    def test_a_tar_past_the_time_bound_is_partial(self, tmp_path, monkeypatch):
        path = tmp_path / "logs.tar"
        _tar(path, 5)
        ticks = iter([0.0, 0.0, 10_000.0, 10_000.0, 10_000.0, 10_000.0])
        result = generic._probe_tar(str(path), _ctx(tmp_path, "logs.tar"), clock=lambda: next(ticks))
        archive = result.attributes["sys_archive"]
        assert archive["partial"] is True and archive["entryCount"] == 1
        assert f"{generic.TAR_SCAN_MAX_SECONDS}-second bound" in archive["listingNote"]

    def test_a_compressed_tar_over_the_download_bound_says_why_it_is_not_listed(self, s3):
        _seed_other(s3, "gzip")
        module = _load(s3)
        event = _event("backup.tar.gz", "other", "application/gzip")
        event["fileSize"] = module.INPUT_MAX_DOWNLOAD_BYTES + 1
        module.lambda_handler(event, None)
        assert s3.downloads == [] and s3.ranged_gets == []
        manifest = s3.manifest(_AUX, _MANIFEST_KEY)
        assert set(manifest["attributes"]) == {"sys_file"} and manifest["renderSkipped"] == "size"
        assert any("tar archive" in warning and "not downloaded" in warning for warning in manifest["warnings"])
