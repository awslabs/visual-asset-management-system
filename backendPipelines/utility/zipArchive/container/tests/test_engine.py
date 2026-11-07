#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Streaming engine: zip (store, deflate, unicode, directories), tar / PAX / tar.gz, the multipart sink
(part-size math, bounded parts, abort), and the ordered prefetch reader."""

import io
import os
import shutil
import subprocess
import tarfile
import tempfile
import zipfile

import pytest
from botocore.exceptions import ClientError

from zip_archive_pipeline.engine.reader import PrefetchReader, RangeReadError
from zip_archive_pipeline.engine.sink import MultipartSink, part_size_bytes
from zip_archive_pipeline.engine.tarstream import TarStreamWriter
from zip_archive_pipeline.engine.zipstream import ZipStreamWriter

pytestmark = pytest.mark.unit
MIB = 1024 * 1024
GIB = 1024 * MIB
TIB = 1024 * GIB


class BytesSink:
    def __init__(self):
        self.buffer = io.BytesIO()

    def write(self, data):
        self.buffer.write(data)


def _zip(members, compression="store", dirs=()):
    sink = BytesSink()
    writer = ZipStreamWriter(sink, compression=compression)
    for d in dirs:
        writer.add_directory(d)
    for name, data in members:
        writer.add_file(name, [data[i:i + 7] for i in range(0, len(data), 7)], len(data))
    writer.close()
    return sink.buffer.getvalue()


class TestZip:
    def test_store_round_trip(self):
        data = _zip([("a.glb", b"x" * 1000), ("sub/b.bin", b"")], dirs=["empty/"])
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            assert z.read("a.glb") == b"x" * 1000
            assert z.read("sub/b.bin") == b""
            assert z.getinfo("empty/").is_dir()
            assert z.getinfo("a.glb").compress_type == zipfile.ZIP_STORED
            assert z.testzip() is None

    def test_deflate_round_trip(self):
        payload = b"repetitive " * 5000
        data = _zip([("t.txt", payload)], compression="deflate")
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            info = z.getinfo("t.txt")
            assert info.compress_type == zipfile.ZIP_DEFLATED and info.compress_size < len(payload)
            assert z.read("t.txt") == payload

    def test_unicode_names(self):
        data = _zip([("模型/Ünïcødé ✓.glb", b"u")])
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            assert z.namelist() == ["模型/Ünïcødé ✓.glb"]
            assert z.getinfo("模型/Ünïcødé ✓.glb").flag_bits & 0x800

    def test_unzip_validates_small_archive(self, tmp_path):
        if not shutil.which("unzip"):
            pytest.skip("unzip is not installed")
        path = tmp_path / "s.zip"
        path.write_bytes(_zip([("é.txt", b"1"), ("b/c.txt", b"22")], compression="deflate", dirs=["d/"]))
        result = subprocess.run(["unzip", "-tqq", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr

    @pytest.mark.parametrize("name", ["/abs", "../x", "a/../b", "a\\b", "", "nul\x00"])
    def test_unsafe_names_refused(self, name):
        with pytest.raises(ValueError):
            ZipStreamWriter(BytesSink()).add_file(name, [b"x"], 1)

    def test_duplicate_names_refused(self):
        writer = ZipStreamWriter(BytesSink())
        writer.add_file("a", [b"x"], 1)
        with pytest.raises(ValueError):
            writer.add_file("a", [b"y"], 1)

    def test_size_change_detected(self):
        with pytest.raises(IOError):
            ZipStreamWriter(BytesSink()).add_file("a", [b"xyz"], 5)

    def test_store_never_buffers_member_data(self):
        """The writer passes data straight through: every sink write is at most one chunk."""
        writes = []

        class Recording:
            def write(self, data):
                writes.append(len(data))

        writer = ZipStreamWriter(Recording())
        writer.add_file("big", (b"z" * MIB for _ in range(8)), 8 * MIB)
        assert max(writes) <= MIB + 200


class TestTar:
    def _tar(self, members, gzip=False, dirs=()):
        sink = BytesSink()
        writer = TarStreamWriter(sink, gzip=gzip)
        for d in dirs:
            writer.add_directory(d)
        for name, data in members:
            writer.add_file(name, [data[i:i + 5] for i in range(0, len(data), 5)], len(data), mtime=10)
        writer.close()
        return sink.buffer.getvalue()

    @pytest.mark.parametrize("gzip", [False, True])
    def test_round_trip(self, gzip):
        long_name = "deep/" + "n" * 150 + "/ünïcode.bin"
        data = self._tar([("a.txt", b"hello"), (long_name, b"\x00" * 600)], gzip=gzip, dirs=["empty/"])
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz" if gzip else "r:") as t:
            assert t.extractfile("a.txt").read() == b"hello"
            assert t.extractfile(long_name).read() == b"\x00" * 600
            assert t.getmember("empty").isdir()

    def test_member_over_8_gib_gets_a_pax_size(self):
        """ustar's octal size field stops below 8 GiB; the member streams through and its pax header
        carries the exact size. Data is counted, not kept: only the headers are parsed back."""
        size = 8 * GIB + 1

        class HeaderSink:
            def __init__(self):
                self.head = bytearray()
                self.total = 0

            def write(self, data):
                if len(self.head) < 8192:
                    self.head += data[:8192 - len(self.head)]
                self.total += len(data)

        block = b"\x5a" * (8 * MIB)

        def chunks():
            remaining = size
            while remaining:
                n = min(len(block), remaining)
                yield block if n == len(block) else block[:n]
                remaining -= n

        sink = HeaderSink()
        writer = TarStreamWriter(sink)
        writer.add_file("huge.bin", chunks(), size)
        writer.close()
        assert sink.total >= size
        with tarfile.open(fileobj=io.BytesIO(bytes(sink.head)), mode="r:") as t:
            member = t.next()
            assert member.name == "huge.bin" and member.size == size
        assert b"size=8589934593" in bytes(sink.head)

    def test_size_change_detected(self):
        with pytest.raises((IOError, tarfile.ReadError, OSError)):
            TarStreamWriter(BytesSink()).add_file("a", [b"toolong"], 3)


class FakeMultipart:
    def __init__(self, fail_part=None):
        self.parts = {}
        self.completed = None
        self.aborted = False
        self.fail_part = fail_part

    def create_multipart_upload(self, Bucket, Key, **kwargs):
        self.created = (Bucket, Key, kwargs)
        return {"UploadId": "up-1"}

    def upload_part(self, Bucket, Key, UploadId, PartNumber, Body):
        if PartNumber == self.fail_part:
            raise ClientError({"Error": {"Code": "SlowDown"}}, "UploadPart")
        self.parts[PartNumber] = Body
        return {"ETag": f"e{PartNumber}"}

    def complete_multipart_upload(self, Bucket, Key, UploadId, MultipartUpload):
        self.completed = MultipartUpload["Parts"]

    def abort_multipart_upload(self, Bucket, Key, UploadId):
        self.aborted = True

    def list_parts(self, Bucket, Key, UploadId, **_):
        if self.aborted:
            raise ClientError({"Error": {"Code": "NoSuchUpload"}}, "ListParts")
        return {"Parts": []}


class TestSink:
    @pytest.mark.parametrize("total,mib", [(0, 16), (GIB, 16), (TIB, 111), (5 * TIB, 552)])
    def test_part_size_math(self, total, mib):
        size = part_size_bytes(total)
        assert size == mib * MIB
        assert size <= 5 * GIB
        assert -(-total // size) <= 9500

    def test_parts_and_complete(self):
        s3 = FakeMultipart()
        sink = MultipartSink(s3, "b", "k", 5 * MIB, max_in_flight=2, content_type="application/zip")
        payload = os.urandom(12 * MIB + 3)
        for i in range(0, len(payload), MIB):
            sink.write(payload[i:i + MIB])
        assert sink.close() == len(payload)
        assert [p["PartNumber"] for p in s3.completed] == [1, 2, 3]
        assert b"".join(s3.parts[n] for n in (1, 2, 3)) == payload
        assert s3.created[2] == {"ContentType": "application/zip"}

    def test_failed_part_aborts(self):
        s3 = FakeMultipart(fail_part=2)
        sink = MultipartSink(s3, "b", "k", 5 * MIB, max_in_flight=1)
        with pytest.raises(ClientError):
            for _ in range(20):
                sink.write(b"x" * MIB)
            sink.close()
        sink.abort()
        assert s3.aborted and s3.completed is None

    def test_part_size_bounds(self):
        with pytest.raises(ValueError):
            MultipartSink(FakeMultipart(), "b", "k", MIB)


class FakeRangeS3:
    def __init__(self, objects, flaky=0):
        self.objects = objects
        self.flaky = flaky
        self.calls = []

    def get_object(self, Bucket, Key, Range, VersionId=None):
        self.calls.append((Key, Range, VersionId))
        if self.flaky:
            self.flaky -= 1
            raise ClientError({"Error": {"Code": "InternalError"}}, "GetObject")
        start, end = (int(v) for v in Range.replace("bytes=", "").split("-"))
        return {"Body": io.BytesIO(self.objects[(Key, VersionId)][start:end + 1])}


class TestReader:
    def test_ordered_ranges_across_members(self):
        objects = {("a", "v1"): os.urandom(25), ("b", None): b"", ("c", "v3"): os.urandom(7)}
        s3 = FakeRangeS3(objects)
        members = [{"bucket": "x", "key": k, "versionId": v, "size": len(d)} for (k, v), d in objects.items()]
        reader = PrefetchReader(lambda b: s3, members, range_size=10, max_bytes_in_flight=20, workers=3)
        out = [b"".join(reader.chunks(i)) for i in range(len(members))]
        reader.close()
        assert out == list(objects.values())
        assert all(v == "v1" for k, _, v in s3.calls if k == "a")

    def test_retries_then_succeeds(self):
        s3 = FakeRangeS3({("a", None): b"abcdef"}, flaky=2)
        reader = PrefetchReader(lambda b: s3, [{"bucket": "x", "key": "a", "size": 6}],
                                backoff_seconds=0)
        assert b"".join(reader.chunks(0)) == b"abcdef"

    def test_short_object_fails(self):
        s3 = FakeRangeS3({("a", None): b"abc"})
        reader = PrefetchReader(lambda b: s3, [{"bucket": "x", "key": "a", "size": 6}], retries=1,
                                backoff_seconds=0)
        with pytest.raises(RangeReadError):
            b"".join(reader.chunks(0))


SEVEN_ZIP = shutil.which("7z") or shutil.which("7za") or shutil.which("7zz")
BSDTAR = shutil.which("bsdtar")


def _tar_bytes(members, gzip=False, dirs=()):
    sink = BytesSink()
    writer = TarStreamWriter(sink, gzip=gzip)
    for d in dirs:
        writer.add_directory(d)
    for name, data in members:
        writer.add_file(name, [data], len(data), mtime=10)
    writer.close()
    return sink.buffer.getvalue()


_READER_MEMBERS = [("é.txt", b"1"), ("deep/" + "n" * 150 + "/ü.bin", b"\x00" * 600), ("b/c.txt", b"22")]


class TestOtherReaders:
    """#1125 L2: the archives open in 7-Zip and libarchive (bsdtar, which macOS ships), not only in
    Python and Info-ZIP. Each check skips when its tool is not installed on the machine running it."""

    @pytest.mark.parametrize("compression", ["store", "deflate"])
    def test_7zip_tests_a_zip(self, tmp_path, compression):
        if not SEVEN_ZIP:
            pytest.skip("7-Zip is not installed")
        path = tmp_path / "s.zip"
        path.write_bytes(_zip(_READER_MEMBERS, compression=compression, dirs=["d/"]))
        result = subprocess.run([SEVEN_ZIP, "t", str(path)], capture_output=True, text=True)
        assert result.returncode == 0 and "Everything is Ok" in result.stdout, result.stdout

    @pytest.mark.parametrize("fmt", ["zip-store", "zip-deflate", "tar", "tar.gz"])
    def test_bsdtar_lists_every_member(self, tmp_path, fmt):
        if not BSDTAR:
            pytest.skip("bsdtar is not installed")
        if fmt.startswith("zip"):
            data = _zip(_READER_MEMBERS, compression=fmt.split("-")[1], dirs=["d/"])
        else:
            data = _tar_bytes(_READER_MEMBERS, gzip=fmt == "tar.gz", dirs=["d/"])
        path = tmp_path / "s.bin"
        path.write_bytes(data)
        result = subprocess.run([BSDTAR, "-tf", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        listed = {line.rstrip("/") for line in result.stdout.splitlines()}
        assert {name for name, _ in _READER_MEMBERS} <= listed
        # And it extracts every byte back.
        out = tmp_path / "x"
        out.mkdir()
        result = subprocess.run([BSDTAR, "-xf", str(path), "-C", str(out)], capture_output=True,
                                text=True)
        assert result.returncode == 0, result.stderr
        for name, payload in _READER_MEMBERS:
            assert (out / name).read_bytes() == payload
