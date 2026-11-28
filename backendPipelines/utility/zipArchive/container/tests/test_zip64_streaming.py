#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""ZIP64 proofs for the streaming zip writer, validated by two independent readers: Python's zipfile
(central directory, sizes, CRC of every member) and Info-ZIP ``unzip -t``.

- a member LARGER THAN 4 GiB (streamed from a repeated pattern, never held in memory), followed by
  members whose local headers sit past the 4 GiB offset;
- an archive with MORE THAN 65,535 entries (ZIP64 end of central directory record and locator).

The archive is written to a temporary file only so the readers can open it; the writer itself only
ever calls ``write`` on its sink. Set ARCHIVE_TEST_TMPDIR to put the multi-GiB file on a disk rather
than the default temporary directory.
"""

import os
import shutil
import subprocess
import tempfile
import time
import zipfile
import zlib

import pytest

from zip_archive_pipeline.engine.zipstream import ZIP64_LIMIT, ZipStreamWriter

MIB = 1024 * 1024
UNZIP = shutil.which("unzip")


class _FileSink:
    def __init__(self, handle):
        self._handle = handle
        self.calls = 0

    def write(self, data):
        self.calls += 1
        self._handle.write(data)


def _pattern_chunks(total, chunk_size=8 * MIB):
    block = bytes((i * 31 + 7) % 251 for i in range(chunk_size))
    remaining = total
    while remaining > 0:
        n = min(chunk_size, remaining)
        yield block if n == chunk_size else block[:n]
        remaining -= n


def _pattern_crc(total, chunk_size=8 * MIB):
    crc = 0
    for chunk in _pattern_chunks(total, chunk_size):
        crc = zlib.crc32(chunk, crc)
    return crc & 0xFFFFFFFF


@pytest.fixture
def workdir():
    path = tempfile.mkdtemp(dir=os.environ.get("ARCHIVE_TEST_TMPDIR") or None)
    yield path
    shutil.rmtree(path, ignore_errors=True)


def _unzip_test(path):
    if not UNZIP:
        pytest.skip("unzip is not installed")
    result = subprocess.run([UNZIP, "-tqq", path], capture_output=True, text=True, timeout=3600)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.unit
@pytest.mark.large
def test_member_larger_than_4_gib_and_offsets_past_4_gib(workdir):
    big = ZIP64_LIMIT + 64 * MIB + 3          # just over 4 GiB, not a multiple of the chunk size
    path = os.path.join(workdir, "big.zip")
    started = time.time()
    with open(path, "wb") as handle:
        writer = ZipStreamWriter(_FileSink(handle))
        writer.add_file("first/small.txt", [b"hello"], 5, mtime=1_700_000_000)
        writer.add_file("models/huge.bin", _pattern_chunks(big), big, mtime=1_700_000_000)
        # These local headers start beyond 0xFFFFFFFF, so their central records need ZIP64 offsets.
        writer.add_file("after/tail.txt", [b"tail-bytes"], 10)
        writer.add_directory("empty-folder/")
        total = writer.close()
    assert os.path.getsize(path) == total > ZIP64_LIMIT

    with zipfile.ZipFile(path) as archive:
        infos = {i.filename: i for i in archive.infolist()}
        assert set(infos) == {"first/small.txt", "models/huge.bin", "after/tail.txt", "empty-folder/"}
        assert infos["models/huge.bin"].file_size == big
        assert infos["models/huge.bin"].CRC == _pattern_crc(big)
        assert infos["after/tail.txt"].header_offset > ZIP64_LIMIT
        assert infos["empty-folder/"].is_dir()
        assert archive.read("after/tail.txt") == b"tail-bytes"
        assert archive.testzip() is None          # reads every member and checks its CRC
    _unzip_test(path)
    print(f"4 GiB+ member archive written and validated in {time.time() - started:.1f}s")


@pytest.mark.unit
@pytest.mark.large
def test_more_than_65535_entries(workdir):
    count = 70_000
    path = os.path.join(workdir, "many.zip")
    with open(path, "wb") as handle:
        writer = ZipStreamWriter(_FileSink(handle))
        for i in range(count):
            data = f"{i}".encode()
            writer.add_file(f"d{i % 97}/file-{i:06d}.txt", [data], len(data))
        writer.close()
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        assert len(names) == count
        assert archive.read("d0/file-000000.txt") == b"0"
        assert archive.read(f"d{(count - 1) % 97}/file-{count - 1:06d}.txt") == str(count - 1).encode()
        assert archive.testzip() is None
    _unzip_test(path)
    # The ZIP64 end of central directory record and its locator are present.
    with open(path, "rb") as handle:
        handle.seek(-200, os.SEEK_END)
        tail = handle.read()
    assert b"PK\x06\x06" in tail and b"PK\x06\x07" in tail
