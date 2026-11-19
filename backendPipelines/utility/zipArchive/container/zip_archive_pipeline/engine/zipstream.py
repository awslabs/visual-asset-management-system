# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Streaming ZIP writer (PKWARE APPNOTE 6.3.x) with ZIP64, writing to any object with ``write``.

Nothing is buffered beyond one compressor's internal state and the central directory records, so an
archive of any size streams to the sink as its members stream in. Each member is written as:

    local file header (general purpose bit 3: sizes and CRC follow the data)
    file data (STORE, or raw DEFLATE)
    data descriptor (signature, CRC-32, compressed size, uncompressed size)

A member whose declared size can reach 0xFFFFFFFF carries a ZIP64 extended information extra field in
its local header and 8-byte sizes in its data descriptor (APPNOTE 4.3.9.2). The central directory
records ZIP64 sizes and offsets for every value that overflows 32 bits, and the archive ends with a
ZIP64 end of central directory record and locator when the entry count passes 65,535 or the
directory's size or offset passes 32 bits (APPNOTE 4.4.1.4). Names are UTF-8 with bit 11 set when
they are not plain ASCII; empty folders are directory entries.
"""

import struct
import time
import zlib

ZIP64_LIMIT = 0xFFFFFFFF
ZIP_FILECOUNT_LIMIT = 0xFFFF

_LOCAL_HEADER = struct.Struct("<IHHHHHIIIHH")
_CENTRAL_HEADER = struct.Struct("<IHHHHHHIIIHHHHHII")
_DATA_DESCRIPTOR_32 = struct.Struct("<IIII")
_DATA_DESCRIPTOR_64 = struct.Struct("<IIQQ")
_EOCD = struct.Struct("<IHHHHIIH")
_ZIP64_EOCD = struct.Struct("<IQHHIIQQQQ")
_ZIP64_LOCATOR = struct.Struct("<IIQI")

SIG_LOCAL = 0x04034B50
SIG_CENTRAL = 0x02014B50
SIG_DESCRIPTOR = 0x08074B50
SIG_EOCD = 0x06054B50
SIG_ZIP64_EOCD = 0x06064B50
SIG_ZIP64_LOCATOR = 0x07064B50

FLAG_DATA_DESCRIPTOR = 0x0008
FLAG_UTF8 = 0x0800
METHOD_STORE = 0
METHOD_DEFLATE = 8
VERSION_DEFAULT = 20
VERSION_ZIP64 = 45
# "Made by" UNIX (3) so external attributes carry permission bits that unzip honours.
MADE_BY = (3 << 8) | VERSION_ZIP64
ZIP64_EXTRA_ID = 0x0001

# A deflate stream of incompressible data can exceed its input by a few bytes per 16 KiB block; a
# member this close to the 32-bit limit is written as ZIP64 so its compressed size cannot overflow.
_DEFLATE_HEADROOM = 1 << 20


def dos_datetime(epoch_seconds):
    """DOS date and time fields; dates before 1980 (the format's epoch) clamp to 1980-01-01."""
    t = time.gmtime(max(int(epoch_seconds or 0), 315532800))
    dos_time = (t.tm_hour << 11) | (t.tm_min << 5) | (t.tm_sec // 2)
    dos_date = ((t.tm_year - 1980) << 9) | (t.tm_mon << 5) | t.tm_mday
    return dos_time, dos_date


class _Entry:
    __slots__ = ("name", "flags", "method", "dos_time", "dos_date", "crc", "compressed",
                 "uncompressed", "offset", "is_dir", "zip64_local")


class ZipStreamWriter:
    """Write a ZIP archive member by member to ``sink`` (an object with ``write(bytes)``).

    ``compression`` is ``"store"`` (the default: 3D formats are already compressed) or ``"deflate"``.
    """

    def __init__(self, sink, compression="store", deflate_level=6):
        if compression not in ("store", "deflate"):
            raise ValueError(f"unknown zip compression: {compression}")
        self._sink = sink
        self._method = METHOD_DEFLATE if compression == "deflate" else METHOD_STORE
        self._level = deflate_level
        self._offset = 0
        self._entries = []
        self._names = set()
        self._closed = False

    @property
    def bytes_written(self):
        return self._offset

    @property
    def entry_count(self):
        return len(self._entries)

    def _write(self, data):
        if data:
            self._sink.write(data)
            self._offset += len(data)

    @staticmethod
    def _encode_name(name):
        if not name or name.startswith("/") or "\\" in name or "\x00" in name:
            raise ValueError("archive member names are relative POSIX paths")
        if any(part in ("..", ".") for part in name.rstrip("/").split("/")):
            raise ValueError("archive member names must not contain '.' or '..' segments")
        try:
            return name.encode("ascii"), 0
        except UnicodeEncodeError:
            return name.encode("utf-8"), FLAG_UTF8

    def _register(self, name):
        if name in self._names:
            raise ValueError("duplicate archive member name")
        self._names.add(name)

    def add_directory(self, name, mtime=0):
        """An empty-folder entry (name ends with '/')."""
        if not name.endswith("/"):
            name += "/"
        self._register(name)
        encoded, flags = self._encode_name(name)
        entry = _Entry()
        entry.name, entry.flags, entry.method = encoded, flags, METHOD_STORE
        entry.dos_time, entry.dos_date = dos_datetime(mtime)
        entry.crc = entry.compressed = entry.uncompressed = 0
        entry.offset, entry.is_dir, entry.zip64_local = self._offset, True, False
        self._write(_LOCAL_HEADER.pack(SIG_LOCAL, VERSION_DEFAULT, flags, METHOD_STORE,
                                       entry.dos_time, entry.dos_date, 0, 0, 0, len(encoded), 0))
        self._write(encoded)
        self._entries.append(entry)

    def add_file(self, name, chunks, size_hint, mtime=0):
        """Stream one member from ``chunks`` (an iterable of bytes). ``size_hint`` is the declared
        uncompressed size (from the object's HEAD); it decides whether the local header is ZIP64 and
        is checked against what arrives. Returns the CRC-32."""
        self._register(name)
        encoded, flags = self._encode_name(name)
        flags |= FLAG_DATA_DESCRIPTOR
        entry = _Entry()
        entry.name, entry.flags, entry.method = encoded, flags, self._method
        entry.dos_time, entry.dos_date = dos_datetime(mtime)
        entry.offset, entry.is_dir = self._offset, False
        headroom = _DEFLATE_HEADROOM if self._method == METHOD_DEFLATE else 0
        entry.zip64_local = int(size_hint) + headroom >= ZIP64_LIMIT
        if entry.zip64_local:
            # Sizes in the local header are the 0xFFFFFFFF marker; the ZIP64 extra field holds zeros
            # (the real values follow in the data descriptor, bit 3).
            extra = struct.pack("<HHQQ", ZIP64_EXTRA_ID, 16, 0, 0)
            header = _LOCAL_HEADER.pack(SIG_LOCAL, VERSION_ZIP64, flags, self._method, entry.dos_time,
                                        entry.dos_date, 0, ZIP64_LIMIT, ZIP64_LIMIT, len(encoded),
                                        len(extra))
            self._write(header + encoded + extra)
        else:
            header = _LOCAL_HEADER.pack(SIG_LOCAL, VERSION_DEFAULT, flags, self._method, entry.dos_time,
                                        entry.dos_date, 0, 0, 0, len(encoded), 0)
            self._write(header + encoded)

        crc, uncompressed, compressed = 0, 0, 0
        compressor = zlib.compressobj(self._level, zlib.DEFLATED, -15) \
            if self._method == METHOD_DEFLATE else None
        for chunk in chunks:
            if not chunk:
                continue
            crc = zlib.crc32(chunk, crc)
            uncompressed += len(chunk)
            data = compressor.compress(chunk) if compressor else chunk
            compressed += len(data)
            self._write(data)
        if compressor:
            tail = compressor.flush()
            compressed += len(tail)
            self._write(tail)
        if uncompressed != int(size_hint):
            raise IOError(f"member size changed while streaming ({uncompressed} != {size_hint})")
        if not entry.zip64_local and (compressed >= ZIP64_LIMIT or uncompressed >= ZIP64_LIMIT):
            raise IOError("member exceeded the 32-bit size its local header declared")
        entry.crc, entry.compressed, entry.uncompressed = crc & 0xFFFFFFFF, compressed, uncompressed
        if entry.zip64_local:
            self._write(_DATA_DESCRIPTOR_64.pack(SIG_DESCRIPTOR, entry.crc, compressed, uncompressed))
        else:
            self._write(_DATA_DESCRIPTOR_32.pack(SIG_DESCRIPTOR, entry.crc, compressed, uncompressed))
        self._entries.append(entry)
        return entry.crc

    def close(self):
        """Write the central directory and the end records. Returns the archive's total size."""
        if self._closed:
            return self._offset
        cd_offset = self._offset
        for entry in self._entries:
            zip64_fields = []
            uncompressed, compressed, offset = entry.uncompressed, entry.compressed, entry.offset
            if uncompressed >= ZIP64_LIMIT or entry.zip64_local:
                zip64_fields.append(uncompressed)
                uncompressed = ZIP64_LIMIT
            if compressed >= ZIP64_LIMIT or entry.zip64_local:
                zip64_fields.append(compressed)
                compressed = ZIP64_LIMIT
            if offset >= ZIP64_LIMIT:
                zip64_fields.append(offset)
                offset = ZIP64_LIMIT
            extra = b""
            if zip64_fields:
                extra = struct.pack("<HH", ZIP64_EXTRA_ID, 8 * len(zip64_fields)) + \
                    struct.pack("<" + "Q" * len(zip64_fields), *zip64_fields)
            version = VERSION_ZIP64 if zip64_fields else VERSION_DEFAULT
            if entry.is_dir:
                external = (0o40755 << 16) | 0x10
            else:
                external = 0o100644 << 16
            self._write(_CENTRAL_HEADER.pack(
                SIG_CENTRAL, MADE_BY, version, entry.flags, entry.method, entry.dos_time,
                entry.dos_date, entry.crc, compressed, uncompressed, len(entry.name), len(extra), 0,
                0, 0, external, offset))
            self._write(entry.name)
            self._write(extra)
        cd_size = self._offset - cd_offset
        count = len(self._entries)
        if count > ZIP_FILECOUNT_LIMIT or cd_offset >= ZIP64_LIMIT or cd_size >= ZIP64_LIMIT:
            zip64_eocd_offset = self._offset
            self._write(_ZIP64_EOCD.pack(SIG_ZIP64_EOCD, _ZIP64_EOCD.size - 12, MADE_BY,
                                         VERSION_ZIP64, 0, 0, count, count, cd_size, cd_offset))
            self._write(_ZIP64_LOCATOR.pack(SIG_ZIP64_LOCATOR, 0, zip64_eocd_offset, 1))
            self._write(_EOCD.pack(SIG_EOCD, 0, 0, min(count, ZIP_FILECOUNT_LIMIT),
                                   min(count, ZIP_FILECOUNT_LIMIT), min(cd_size, ZIP64_LIMIT),
                                   min(cd_offset, ZIP64_LIMIT), 0))
        else:
            self._write(_EOCD.pack(SIG_EOCD, 0, 0, count, count, cd_size, cd_offset, 0))
        self._closed = True
        return self._offset
