# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Streaming tar / tar.gz writer over the standard library's ``tarfile`` in stream mode.

POSIX pax format: names longer than the ustar fields, non-ASCII names and members of 8 GiB or more
(the ustar size field's octal limit) get a pax extended header, so any member size S3 can hold
round-trips. The tar is written in plain write mode to a forward-only file object over the sink (and
through ``gzip.GzipFile`` for tar.gz); neither ever seeks while writing.
"""

import gzip as _gzip
import tarfile


class _ChunkReader:
    """A read()-able view over an iterable of byte chunks, for ``tarfile.addfile``."""

    def __init__(self, chunks):
        self._iter = iter(chunks)
        self._chunk = memoryview(b"")
        self._pos = 0
        self.consumed = 0

    def read(self, size=-1):
        # tarfile copies in small blocks, so the current chunk is read through a moving offset
        # rather than re-sliced (a 32 MiB chunk read 16 KiB at a time would otherwise be copied
        # thousands of times).
        out = []
        wanted = size if size >= 0 else None
        while wanted is None or wanted > 0:
            if self._pos >= len(self._chunk):
                try:
                    self._chunk, self._pos = memoryview(next(self._iter)), 0
                except StopIteration:
                    break
                continue
            take = len(self._chunk) - self._pos if wanted is None else min(wanted, len(self._chunk) - self._pos)
            out.append(self._chunk[self._pos:self._pos + take].tobytes())
            self._pos += take
            if wanted is not None:
                wanted -= take
        data = b"".join(out)
        self.consumed += len(data)
        return data


class _SinkFile:
    """A write-only, forward-only file object over the sink. ``tarfile`` in plain write mode and
    ``gzip.GzipFile`` only ever call ``write`` and ``tell`` on it (they never seek when writing)."""

    def __init__(self, sink):
        self._sink = sink
        self.written = 0

    def write(self, data):
        if data:
            self._sink.write(bytes(data))
            self.written += len(data)
        return len(data)

    def tell(self):
        return self.written

    def flush(self):
        pass

    def close(self):
        pass


class TarStreamWriter:
    def __init__(self, sink, gzip=False):
        self._file = _SinkFile(sink)
        # Plain write mode rather than the "w|" stream mode: stream mode re-slices its buffer once per
        # 10 KiB record, which is quadratic in the size of each write.
        self._gzip = _gzip.GzipFile(fileobj=self._file, mode="wb", compresslevel=6, mtime=0) \
            if gzip else None
        self._tar = tarfile.open(fileobj=self._gzip or self._file, mode="w", format=tarfile.PAX_FORMAT)
        # tarfile copies member data in 16 KiB blocks by default, which is millions of Python calls
        # per TiB; copy in 8 MiB blocks instead (memory stays bounded by one block).
        self._tar.copybufsize = 8 * 1024 * 1024
        self._names = set()
        self._count = 0

    @property
    def bytes_written(self):
        return self._file.written

    @property
    def entry_count(self):
        return self._count

    def _check(self, name):
        if not name or name.startswith("/") or "\\" in name or "\x00" in name:
            raise ValueError("archive member names are relative POSIX paths")
        if any(part in ("..", ".") for part in name.rstrip("/").split("/")):
            raise ValueError("archive member names must not contain '.' or '..' segments")
        if name in self._names:
            raise ValueError("duplicate archive member name")
        self._names.add(name)

    def add_directory(self, name, mtime=0):
        name = name.rstrip("/")
        self._check(name + "/")
        info = tarfile.TarInfo(name)
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
        info.mtime = int(mtime or 0)
        self._tar.addfile(info)
        self._count += 1

    def add_file(self, name, chunks, size_hint, mtime=0):
        self._check(name)
        info = tarfile.TarInfo(name)
        info.size = int(size_hint)
        info.mode = 0o644
        info.mtime = int(mtime or 0)
        reader = _ChunkReader(chunks)
        self._tar.addfile(info, reader)
        # tarfile reads exactly info.size bytes (and raises on a short source); anything left over
        # means the object grew while it streamed.
        if reader.read(1) or reader.consumed != info.size:
            raise IOError("member size changed while streaming")
        self._count += 1

    def close(self):
        self._tar.close()
        if self._gzip is not None:
            self._gzip.close()
        return self._file.written
