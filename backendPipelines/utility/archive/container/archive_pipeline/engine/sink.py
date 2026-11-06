# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bounded-memory S3 multipart upload sink.

Bytes written are cut into parts of a fixed size and uploaded by a small worker pool. At most
``max_in_flight`` parts are queued or uploading at once, so memory is bounded by
``part_size * (max_in_flight + 1)`` whatever the archive's size. The upload is aborted on any error
(and by ``abort()``, which the SIGTERM handler calls), so a failed archive leaves no billable parts.
"""

import math
import threading
from concurrent.futures import ThreadPoolExecutor

from botocore.exceptions import ClientError

MIB = 1024 * 1024
MIN_PART_SIZE = 16 * MIB
MAX_PART_SIZE = 5 * 1024 * MIB
MAX_PARTS = 10000
TARGET_PARTS = 9500


def part_size_bytes(estimated_total_bytes):
    """At least 16 MiB, big enough to stay within 9,500 parts (headroom under S3's 10,000), rounded up
    to 1 MiB and capped at S3's 5 GiB part maximum. Mirrors backend common/zipDownloads.part_size_bytes."""
    size = max(MIN_PART_SIZE, math.ceil(max(int(estimated_total_bytes), 0) / TARGET_PARTS))
    size = math.ceil(size / MIB) * MIB
    return min(size, MAX_PART_SIZE)


class MultipartSink:
    def __init__(self, s3_client, bucket, key, part_size, max_in_flight=4, content_type="",
                 extra_args=None, logger=None):
        if part_size < 5 * MIB or part_size > MAX_PART_SIZE:
            raise ValueError("part size outside S3's limits")
        self._s3, self.bucket, self.key = s3_client, bucket, key
        self.part_size = int(part_size)
        self._logger = logger
        args = dict(extra_args or {})
        if content_type:
            args["ContentType"] = content_type
        response = self._s3.create_multipart_upload(Bucket=bucket, Key=key, **args)
        self.upload_id = response["UploadId"]
        self._buffer = bytearray()
        self._parts = {}
        self._next_part = 1
        self._pool = ThreadPoolExecutor(max_workers=max_in_flight)
        self._slots = threading.BoundedSemaphore(max_in_flight)
        self._futures = []
        self._error = None
        self._lock = threading.Lock()
        self._done = False
        self.bytes_written = 0
        self.version_id = None

    def _upload(self, number, data):
        try:
            response = self._s3.upload_part(Bucket=self.bucket, Key=self.key, UploadId=self.upload_id,
                                            PartNumber=number, Body=bytes(data))
            with self._lock:
                self._parts[number] = response["ETag"]
        except Exception as e:  # recorded and re-raised on the writer thread
            with self._lock:
                self._error = self._error or e
        finally:
            self._slots.release()

    def _raise_if_failed(self):
        if self._error is not None:
            raise self._error

    def _submit(self, data):
        if self._next_part > MAX_PARTS:
            raise IOError("archive needs more than 10,000 parts at this part size")
        self._slots.acquire()
        self._raise_if_failed()
        number = self._next_part
        self._next_part += 1
        self._futures.append(self._pool.submit(self._upload, number, data))

    def write(self, data):
        if self._done:
            raise IOError("write after the upload finished")
        self._raise_if_failed()
        self._buffer += data
        self.bytes_written += len(data)
        while len(self._buffer) >= self.part_size:
            part = bytes(self._buffer[:self.part_size])
            del self._buffer[:self.part_size]
            self._submit(part)

    def close(self):
        """Upload the last part and complete the upload. Returns the object's size."""
        self._raise_if_failed()
        if self._buffer or self._next_part == 1:
            self._submit(bytes(self._buffer))
            self._buffer = bytearray()
        for future in self._futures:
            future.result()
        self._raise_if_failed()
        parts = [{"PartNumber": n, "ETag": self._parts[n]} for n in sorted(self._parts)]
        response = self._s3.complete_multipart_upload(Bucket=self.bucket, Key=self.key,
                                                      UploadId=self.upload_id,
                                                      MultipartUpload={"Parts": parts})
        # The object version this upload created (None on an unversioned bucket), so the job can
        # delete exactly its own archive if it finds the job finalized without it.
        self.version_id = (response or {}).get("VersionId")
        self._done = True
        self._pool.shutdown(wait=True)
        return self.bytes_written

    def object_exists(self):
        """Whether an object exists at the upload's key (HEAD; raises on errors other than 404)."""
        try:
            self._s3.head_object(Bucket=self.bucket, Key=self.key)
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def delete_landed(self):
        """Delete the object version this upload created (the key's current object on an
        unversioned bucket)."""
        args = {"Bucket": self.bucket, "Key": self.key}
        if self.version_id:
            args["VersionId"] = self.version_id
        self._s3.delete_object(**args)

    def abort(self):
        """Abort the upload (idempotent, never raises)."""
        if self._done:
            return
        self._done = True
        try:
            self._pool.shutdown(wait=False, cancel_futures=True)
        except Exception:  # nosec B110 - abort is best-effort; the lifecycle rule is the backstop
            pass
        try:
            self._s3.abort_multipart_upload(Bucket=self.bucket, Key=self.key, UploadId=self.upload_id)
        except Exception as e:  # nosec B110 - abort is best-effort; the lifecycle rule is the backstop
            if self._logger:
                self._logger.warning(f"Could not abort the multipart upload: {e}")
