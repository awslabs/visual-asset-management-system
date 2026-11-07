# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bounded-memory S3 multipart upload sink.

Bytes written are cut into parts of a fixed size and uploaded by a small worker pool. At most
``max_in_flight`` parts are queued or uploading at once, and the writer blocks with the next part in
hand while all slots are taken. The buffer holds less than one part plus the last write, and cutting a
part from it makes one copy of the part. So the sink's own peak memory is about
``part_size * (max_in_flight + 2)`` plus one write, whatever the archive's size: with the largest
part size (about 584 MiB for a 5 TiB archive) and four in flight, about 3.5 GiB, to which the HTTP
client's buffers and the reader's prefetch add. The upload is aborted on any error
(and by ``abort()``, which the SIGTERM handler calls), so a failed archive leaves no billable parts.

No part is left behind after a stop (the #1123 D5 rule, applied to this sink). S3 keeps a part whose
UploadPart was in flight during an abort, and the upload then stays listed with that part (an upload
"may need aborting more than once"). So each part first asks ``should_stop`` (the job's state: the
user's cancel, another writer's finalization, the SIGTERM) and uploads nothing once it answers True;
``abort()`` cancels the queued parts, aborts, waits a bounded time for the parts in flight, then aborts
until ListParts answers NoSuchUpload (bounded); and a part that ends after an abort aborts again
itself. A completed upload is gone as an upload (NoSuchUpload) and its object stays, so a landed
archive is never touched by the sweep.
"""

import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from botocore.exceptions import ClientError

MIB = 1024 * 1024
MIN_PART_SIZE = 16 * MIB
MAX_PART_SIZE = 5 * 1024 * MIB
MAX_PARTS = 10000
TARGET_PARTS = 9500
# Bounded abort sweep after a stop (mirrors backend common/largeObjectCopy.abort_until_gone). Kept short
# because the SIGTERM handler runs it before AWS Batch's SIGKILL; a part still in flight past the
# bound aborts again when it ends (_upload), and the prepared-downloads lifecycle rule is the backstop.
ABORT_SWEEP_ATTEMPTS = 4
ABORT_SWEEP_WAIT_SECONDS = 2
IN_FLIGHT_WAIT_SECONDS = 10


class StopRequested(IOError):
    """A part was not uploaded because ``should_stop`` answered True (the job was stopped or ended)."""


def part_size_bytes(estimated_total_bytes):
    """At least 16 MiB, big enough to stay within 9,500 parts (headroom under S3's 10,000), rounded up
    to 1 MiB and capped at S3's 5 GiB part maximum. Mirrors backend common/zipDownloads.part_size_bytes."""
    size = max(MIN_PART_SIZE, math.ceil(max(int(estimated_total_bytes), 0) / TARGET_PARTS))
    size = math.ceil(size / MIB) * MIB
    return min(size, MAX_PART_SIZE)


class MultipartSink:
    def __init__(self, s3_client, bucket, key, part_size, max_in_flight=4, content_type="",
                 extra_args=None, logger=None, should_stop=None, sleep=None,
                 in_flight_wait_seconds=IN_FLIGHT_WAIT_SECONDS, on_created=None):
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
        if on_created is not None:
            # The job records the upload id before any part is uploaded, so a stopper's delayed sweep
            # can abort exactly this upload later (#1125 D16, the #1123 D5 rule: an abort's immediate
            # answer is not final). An upload that could not be recorded is aborted at once.
            try:
                on_created(self.upload_id)
            except BaseException:
                try:
                    self._s3.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=self.upload_id)
                except Exception as e:  # nosec B110 - the lifecycle rule is the backstop
                    self._warn(f"Could not abort an unrecorded multipart upload: {e!r}")
                raise
        self._buffer = bytearray()
        self._parts = {}
        self._next_part = 1
        self._pool = ThreadPoolExecutor(max_workers=max_in_flight)
        self._slots = threading.BoundedSemaphore(max_in_flight)
        self._futures = []
        self._error = None
        # Re-entrant: the SIGTERM handler can call abort() on the main thread while it is in abort().
        self._lock = threading.RLock()
        self._idle = threading.Condition(self._lock)
        self._in_flight = 0
        self._aborted = False
        self._should_stop = should_stop
        self._sleep = sleep or time.sleep
        self._in_flight_wait = in_flight_wait_seconds
        self._done = False
        self.bytes_written = 0
        self.version_id = None
        # True while CompleteMultipartUpload is in flight. S3 does not refuse an abort sent then: it
        # answers 204 and the Complete can still succeed (#1123 D1), so a stop must not treat its
        # own abort as "nothing will land" (runner.Job.stop).
        self.completing = False

    def _warn(self, message):
        if self._logger:
            self._logger.warning(message)

    def _stop_requested(self):
        """Whether no further part may be uploaded: this sink was aborted, or ``should_stop`` answers
        True. A failed state check does not stop the archive (logged); the finalizers' abort sweep
        and the job row's conditional writes still decide the outcome."""
        if self._aborted:
            return True
        if self._should_stop is None:
            return False
        try:
            return bool(self._should_stop())
        except Exception as e:
            self._warn(f"Could not check the job's state before a part: {e!r}")
            return False

    def _upload(self, number, data):
        try:
            if self._stop_requested():
                raise StopRequested("The archive job was stopped")
            with self._lock:
                self._in_flight += 1
            try:
                response = self._s3.upload_part(Bucket=self.bucket, Key=self.key,
                                                UploadId=self.upload_id, PartNumber=number,
                                                Body=bytes(data))
            finally:
                with self._lock:
                    self._in_flight -= 1
                    late = self._aborted
                    self._idle.notify_all()
            if late:
                # This part may have landed after the abort and keeps the upload listed: abort again
                # now that its own upload ended.
                self._abort_until_gone()
                raise StopRequested("The archive job was stopped")
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
            # One copy of the part (a bytearray slice would copy it twice); the view is released
            # before the buffer is resized.
            with memoryview(self._buffer) as view:
                part = bytes(view[:self.part_size])
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
        self.completing = True
        try:
            response = self._s3.complete_multipart_upload(Bucket=self.bucket, Key=self.key,
                                                          UploadId=self.upload_id,
                                                          MultipartUpload={"Parts": parts})
        finally:
            self.completing = False
        # The object version this upload created (None on an unversioned bucket), so the job can
        # delete exactly its own archive if it finds the job finalized without it.
        self.version_id = (response or {}).get("VersionId")
        self._done = True
        self._pool.shutdown(wait=True)
        return self.bytes_written

    def head(self):
        """HEAD of the key's current object, or None when there is none (raises on other errors)."""
        try:
            return self._s3.head_object(Bucket=self.bucket, Key=self.key)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    def delete_version(self, version_id):
        """Delete exactly one object version of the key."""
        self._s3.delete_object(Bucket=self.bucket, Key=self.key, VersionId=version_id)

    def _abort_until_gone(self, aborted=False):
        """Abort until ListParts answers NoSuchUpload, at most ABORT_SWEEP_ATTEMPTS aborts, waiting
        only while the upload is still listed. ``aborted``: the caller has just aborted, so the sweep
        checks first. Returns True when the upload is gone. Never raises (logged; the
        prepared-downloads lifecycle rule is the backstop)."""
        for attempt in range(ABORT_SWEEP_ATTEMPTS):
            if attempt or not aborted:
                if attempt:
                    self._sleep(ABORT_SWEEP_WAIT_SECONDS)
                try:
                    self._s3.abort_multipart_upload(Bucket=self.bucket, Key=self.key,
                                                    UploadId=self.upload_id)
                except ClientError as e:
                    if e.response.get("Error", {}).get("Code") == "NoSuchUpload":
                        return True
                    self._warn(f"Could not abort the multipart upload: {e}")
                    return False
                except Exception as e:
                    self._warn(f"Could not abort the multipart upload: {e!r}")
                    return False
            try:
                self._s3.list_parts(Bucket=self.bucket, Key=self.key, UploadId=self.upload_id,
                                    MaxParts=1)
            except ClientError as e:
                if e.response.get("Error", {}).get("Code") == "NoSuchUpload":
                    return True
                self._warn(f"Could not check that the multipart upload is gone: {e}")
                return False
            except Exception as e:
                self._warn(f"Could not check that the multipart upload is gone: {e!r}")
                return False
        self._warn(f"The multipart upload is still listed after {ABORT_SWEEP_ATTEMPTS} aborts; a part "
                   f"still in flight aborts it again when it ends")
        return False

    def abort(self):
        """Abort the upload so that no part stays (idempotent, never raises): cancel the queued
        parts, abort, wait up to ``in_flight_wait_seconds`` for the parts in flight, then abort until
        the upload is gone. A completed upload is left alone (its archive is the job's decision)."""
        with self._lock:
            if self._done:
                return
            self._done = True
            self._aborted = True
        try:
            self._pool.shutdown(wait=False, cancel_futures=True)
        except Exception:  # nosec B110 - abort is best-effort; the lifecycle rule is the backstop
            pass
        aborted = False
        try:
            self._s3.abort_multipart_upload(Bucket=self.bucket, Key=self.key, UploadId=self.upload_id)
            aborted = True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "NoSuchUpload":
                aborted = True
            else:
                self._warn(f"Could not abort the multipart upload: {e}")
        except Exception as e:  # nosec B110 - abort is best-effort; the lifecycle rule is the backstop
            self._warn(f"Could not abort the multipart upload: {e!r}")
        deadline = time.monotonic() + max(0.0, float(self._in_flight_wait))
        with self._lock:
            while self._in_flight > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._idle.wait(remaining)
        self._abort_until_gone(aborted=aborted)
