# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Ordered, bounded prefetch of many S3 objects as byte ranges.

Every member is split into ranges of ``range_size``; ranges of the current and the following members
are fetched by a worker pool while earlier ones are consumed, and handed back strictly in order. At
most ``max_bytes_in_flight`` bytes are fetched ahead, so memory stays bounded while many small files
and the parts of large ones download in parallel. Each range is a ``GetObject`` of the member's pinned
``VersionId`` with a ``Range`` header, retried with backoff, and its length is checked, so an object
replaced or shortened mid-run fails the archive instead of corrupting it.
"""

import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

MIB = 1024 * 1024


class RangeReadError(IOError):
    pass


class PrefetchReader:
    def __init__(self, get_client, members, range_size=32 * MIB, max_bytes_in_flight=512 * MIB,
                 workers=16, retries=5, backoff_seconds=1.0):
        """``members``: list of dicts with ``bucket``, ``key``, ``versionId`` (may be None) and
        ``size``. ``get_client(bucket)`` returns the S3 client for that bucket's Region."""
        self._get_client = get_client
        self._members = members
        self._range_size = max(int(range_size), 1)
        self._budget = max(int(max_bytes_in_flight), self._range_size)
        self._retries = retries
        self._backoff = backoff_seconds
        self._pool = ThreadPoolExecutor(max_workers=workers)
        self._tasks = self._plan()
        self._queue = deque()
        self._next_task = 0
        self._in_flight_bytes = 0
        self._lock = threading.Lock()

    def _plan(self):
        tasks = []
        for index, member in enumerate(self._members):
            size = int(member["size"])
            start = 0
            while start < size:
                end = min(start + self._range_size, size) - 1
                tasks.append((index, start, end))
                start = end + 1
        return tasks

    def _fetch(self, member, start, end):
        expected = end - start + 1
        params = {"Bucket": member["bucket"], "Key": member["key"], "Range": f"bytes={start}-{end}"}
        if member.get("versionId"):
            params["VersionId"] = member["versionId"]
        last_error = None
        for attempt in range(self._retries + 1):
            try:
                body = self._get_client(member["bucket"]).get_object(**params)["Body"]
                try:
                    data = body.read()
                finally:
                    body.close()
                if len(data) != expected:
                    raise RangeReadError(f"short read ({len(data)} of {expected} bytes)")
                return data
            except Exception as e:
                last_error = e
                if attempt < self._retries:
                    time.sleep(self._backoff * (2 ** attempt))
        raise RangeReadError(f"could not read {member['key']}: {last_error}")

    def _fill(self):
        while self._next_task < len(self._tasks):
            index, start, end = self._tasks[self._next_task]
            size = end - start + 1
            if self._queue and self._in_flight_bytes + size > self._budget:
                return
            future = self._pool.submit(self._fetch, self._members[index], start, end)
            self._queue.append((index, size, future))
            self._in_flight_bytes += size
            self._next_task += 1

    def chunks(self, index):
        """The byte chunks of member ``index``, in order. Members must be consumed in order."""
        size = int(self._members[index]["size"])
        if size == 0:
            return
        remaining = size
        while remaining > 0:
            self._fill()
            if not self._queue:
                raise RangeReadError("prefetch queue drained before the member ended")
            task_index, task_size, future = self._queue.popleft()
            if task_index != index:
                raise RangeReadError("members consumed out of order")
            data = future.result()
            self._in_flight_bytes -= task_size
            remaining -= len(data)
            yield data

    def close(self):
        for _, _, future in self._queue:
            future.cancel()
        self._pool.shutdown(wait=False, cancel_futures=True)
