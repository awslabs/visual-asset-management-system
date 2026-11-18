# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A read-only, seekable file object over one Amazon S3 object version, read by ranged GETs.

It lets a reader that seeks (`zipfile`) take the few regions of a large object it needs (a zip container's
end-of-central-directory record and its central directory) without downloading the object. Each GET names a
byte range of the object at the pinned version; a read is served from the bytes a previous GET returned when it
falls inside them, and a read outside them fetches at least `read_ahead` bytes, so a run of small reads (a zip
member's local header, then its data) costs few requests. The reader is bounded: a GET that would take it past
`max_bytes` in total, or past `max_gets` requests, raises RangedReadBudgetExceeded instead, so no input can make it
fetch an object piece by piece. `gets` and `bytes_read` count what it fetched, for the caller's log line.

The client is the one for the bucket's Region (s3_regions.client_for_bucket); the caller passes the object size it
already knows (the state's fileSize, from the HeadObject constructPipeline made at the same version), else a
HeadObject at that version supplies it.
"""

import io
from typing import List, Optional, Tuple

# Bytes fetched at least per GET, so the small reads that follow one another cost one request.
RANGED_READ_AHEAD_BYTES = 256 * 1024
# The most one reader fetches and the most requests it makes: a zip listing reads the tail, a central directory of
# at most 64 MiB (common.ZIP_CENTRAL_DIRECTORY_MAX_BYTES) and two OPC parts of at most 4 MiB each.
RANGED_READ_MAX_BYTES = 80 * 1024 * 1024
RANGED_READ_MAX_GETS = 64
# The fetched blocks kept for later reads (the tail, the directory, a member's region).
_KEPT_BLOCKS = 4


class RangedReadBudgetExceeded(OSError):
    """A read would take the ranged reader past its byte or request bound."""


class RangedS3Reader(io.RawIOBase):
    """``read``/``seek``/``tell`` over s3://bucket/key at ``version_id`` by ranged GETs (see the module notes)."""

    def __init__(self, client, bucket: str, key: str, version_id: Optional[str] = None, size: Optional[int] = None,
                 read_ahead: int = RANGED_READ_AHEAD_BYTES, max_bytes: int = RANGED_READ_MAX_BYTES,
                 max_gets: int = RANGED_READ_MAX_GETS):
        super().__init__()
        self._client = client
        self._bucket = bucket
        self._key = key
        self._version_kwargs = {"VersionId": version_id} if version_id else {}
        self._read_ahead = max(1, int(read_ahead))
        self._max_bytes = int(max_bytes)
        self._max_gets = int(max_gets)
        self._blocks: List[Tuple[int, bytes]] = []
        self._pos = 0
        self.gets = 0
        self.bytes_read = 0
        if size is None or int(size) <= 0:
            head = client.head_object(Bucket=bucket, Key=key, **self._version_kwargs)
            size = int(head.get("ContentLength", 0) or 0)
        self.size = int(size)

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            position = offset
        elif whence == io.SEEK_CUR:
            position = self._pos + offset
        elif whence == io.SEEK_END:
            position = self.size + offset
        else:
            raise ValueError(f"invalid whence ({whence})")
        if position < 0:
            raise ValueError("negative seek position")
        self._pos = position
        return position

    def read(self, size: int = -1) -> bytes:
        if self._pos >= self.size:
            return b""
        end = self.size if size is None or size < 0 else min(self.size, self._pos + size)
        data = self._range(self._pos, end)
        self._pos = end
        return data

    def readinto(self, buffer) -> int:
        data = self.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)

    def readall(self) -> bytes:
        return self.read(-1)

    def _range(self, start: int, end: int) -> bytes:
        """Bytes [start, end) of the object, from a kept block when one holds them."""
        for block_start, block in self._blocks:
            if block_start <= start and end <= block_start + len(block):
                return block[start - block_start:end - block_start]
        fetch_end = min(self.size, max(end, start + self._read_ahead))
        block = self._get(start, fetch_end)
        self._blocks.append((start, block))
        del self._blocks[:-_KEPT_BLOCKS]
        return block[:end - start]

    def _get(self, start: int, end: int) -> bytes:
        if self.gets >= self._max_gets or self.bytes_read + (end - start) > self._max_bytes:
            raise RangedReadBudgetExceeded(
                f"reading bytes {start}-{end - 1} would exceed the ranged-read bound "
                f"({self._max_gets} requests, {self._max_bytes:,} bytes; {self.gets} requests and "
                f"{self.bytes_read:,} bytes so far)")
        response = self._client.get_object(Bucket=self._bucket, Key=self._key, Range=f"bytes={start}-{end - 1}",
                                           **self._version_kwargs)
        data = response["Body"].read()
        self.gets += 1
        self.bytes_read += len(data)
        if len(data) != end - start:
            raise OSError(f"ranged read of bytes {start}-{end - 1} returned {len(data)} bytes")
        return data
