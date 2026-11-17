#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""#1125 in-dev D16: a stopped archive leaves no part behind in vams.preparedDownloads/.

The large object copy engine's #1123 D5 showed that S3 keeps a part whose upload was in flight during
an abort, and the upload then stays listed with that part. The zipArchive sink aborted once
(``shutdown(wait=False)`` then one AbortMultipartUpload), so a part still uploading when the user
cancelled, a finalizer aborted or AWS Batch sent SIGTERM kept the upload listed until the bucket's
lifecycle rule. It also uploaded every part whatever the job's state.

Now each part reads the job first (``should_stop``; mode 1: status.must_stop, the row ended, owned by
another run, or carrying a stopper's stopRequestedAt) and uploads nothing once it must stop;
``abort()`` waits a bounded time for the parts in flight and aborts until ListParts answers
NoSuchUpload; and a part that ends after an abort aborts again itself.

``LateS3`` gives FakeS3 S3's documented abort behaviour: an abort sent while a part upload is in
flight answers 204 and leaves the upload (and the part, which lands) in place. Every test fails on
dc9f8d591 except two guards that pass there too: a completed upload is left alone, and a failed
state read does not stop the archive."""

import threading

import pytest

from zip_archive_pipeline import runner
from zip_archive_pipeline.engine import sink as sink_module
from zip_archive_pipeline.engine.sink import MIB, MultipartSink, StopRequested
from zip_archive_pipeline.status import StatusWriter
from test_runner import AUX, BASE, FakeS3, _manifest, _zip_job

pytestmark = pytest.mark.unit

KEY = "vams.preparedDownloads/db1/asset1/h/r1/out.zip"


class LateS3(FakeS3):
    """FakeS3 with S3's abort semantics for part uploads in flight. ``during_part`` maps a part number
    to a callable run while that part's upload is in flight."""

    def __init__(self):
        super().__init__()
        self.in_flight = 0
        self.during_part = {}
        self.aborts_in_flight = 0
        self.parts_uploaded = []
        self._lock = threading.Lock()

    def upload_part(self, Bucket, Key, UploadId, PartNumber, Body):
        if (Bucket, Key) not in self.uploads:
            raise runner_err("NoSuchUpload")
        with self._lock:
            self.in_flight += 1
        try:
            hook = self.during_part.pop(PartNumber, None)
            if hook:
                hook()
            # The part lands even when an abort arrived while it was in flight.
            self.uploads.setdefault((Bucket, Key), {})[PartNumber] = Body
            self.parts_uploaded.append(PartNumber)
            return {"ETag": str(PartNumber)}
        finally:
            with self._lock:
                self.in_flight -= 1

    def abort_multipart_upload(self, Bucket, Key, UploadId):
        self.aborted.append(Key)
        if (Bucket, Key) not in self.uploads:
            raise runner_err("NoSuchUpload")
        with self._lock:
            busy = self.in_flight > 0
        if busy:
            self.aborts_in_flight += 1
            return {"ResponseMetadata": {"HTTPStatusCode": 204}}
        self.uploads.pop((Bucket, Key), None)
        return {"ResponseMetadata": {"HTTPStatusCode": 204}}


def runner_err(code):
    from test_runner import _err
    return _err(code)


def _open_uploads(s3):
    return [k for (b, k) in s3.uploads if b == AUX]


def _sink(s3, **kwargs):
    kwargs.setdefault("max_in_flight", 1)
    kwargs.setdefault("sleep", lambda seconds: None)
    return MultipartSink(s3, AUX, KEY, 5 * MIB, **kwargs)


class TestAbortLeavesNoPart:
    def test_a_part_in_flight_during_the_abort_is_aborted_again(self):
        s3 = LateS3()
        started, release = threading.Event(), threading.Event()

        def hold():
            started.set()
            release.wait(5)
        s3.during_part[1] = hold
        sink = _sink(s3)
        sink.write(b"x" * (5 * MIB))           # part 1 starts uploading
        assert started.wait(5)
        aborter = threading.Thread(target=sink.abort)
        aborter.start()                         # the stop: aborts while part 1 is in flight
        threading.Timer(0.2, release.set).start()
        aborter.join(10)
        assert not aborter.is_alive()
        assert s3.aborts_in_flight >= 1, "the first abort was answered while the part was in flight"
        assert s3.parts_uploaded == [1], "the part landed after the abort"
        assert _open_uploads(s3) == [], "the sweep aborted again once the part ended"

    def test_a_part_outlasting_the_wait_aborts_the_upload_itself_when_it_ends(self):
        s3 = LateS3()
        started, release = threading.Event(), threading.Event()

        def hold():
            started.set()
            release.wait(5)
        s3.during_part[1] = hold
        sink = _sink(s3, in_flight_wait_seconds=0)
        sink.write(b"x" * (5 * MIB))
        assert started.wait(5)
        sink.abort()                            # gives up waiting: the upload is still listed
        assert _open_uploads(s3) == [KEY]
        release.set()
        sink._pool.shutdown(wait=True)          # the part's own upload ends ...
        assert _open_uploads(s3) == [], "... and it aborted the upload again itself"

    def test_the_sweep_is_bounded_and_never_raises(self):
        s3 = LateS3()
        sink = _sink(s3, in_flight_wait_seconds=0)
        s3.in_flight = 1                        # a part that never ends
        sink.abort()
        assert len(s3.aborted) == sink_module.ABORT_SWEEP_ATTEMPTS
        assert _open_uploads(s3) == [KEY]

    def test_a_completed_upload_is_left_alone(self):
        s3 = LateS3()
        sink = _sink(s3)
        sink.write(b"x" * 10)
        sink.close()
        sink.abort()
        assert s3.aborted == [] and (AUX, KEY) in s3.completed


class TestEachPartReadsTheJob:
    def test_no_part_after_the_job_must_stop(self):
        s3 = LateS3()
        stop = {"now": False}
        sink = _sink(s3, should_stop=lambda: stop["now"])
        sink.write(b"x" * (5 * MIB))            # part 1
        sink._futures[0].result(5)
        stop["now"] = True
        with pytest.raises(StopRequested):
            sink.write(b"y" * (5 * MIB))        # part 2 reads the job and is never uploaded
            sink.close()
        sink.abort()
        assert s3.parts_uploaded == [1]
        assert _open_uploads(s3) == []

    def test_a_failed_state_read_does_not_stop_the_archive(self):
        s3 = LateS3()

        def broken():
            raise RuntimeError("throttled")
        sink = _sink(s3, should_stop=broken)
        sink.write(b"x" * 10)
        sink.close()
        assert (AUX, KEY) in s3.completed

    @pytest.mark.parametrize("row,stops", [
        ({"status": "RUNNING", "workflowExecutionId": "exec-1"}, False),
        ({"status": "QUEUED"}, False),
        ({"status": "RUNNING", "workflowExecutionId": "exec-1", "stopRequestedAt": "t"}, True),
        ({"status": "CANCELLED", "workflowExecutionId": "exec-1"}, True),
        ({"status": "FAILED", "workflowExecutionId": "exec-1"}, True),
        ({"status": "RUNNING", "workflowExecutionId": "exec-2"}, True),
        (None, True),
    ])
    def test_must_stop(self, row, stops):
        from test_runner import FakeTable
        assert StatusWriter(FakeTable(row), "alice", "r1", "exec-1").must_stop() is stops


class TestTheJobStopsQuietlyForItsStopper:
    def test_a_recorded_stop_uploads_no_part_and_writes_no_outcome(self, monkeypatch):
        s3 = LateS3()
        s3.add(BASE + "a.glb", b"A" * 100)
        job, table = _zip_job(s3, _manifest(entries=[{"type": "asset", "key": BASE, "relativePath": "/"}]))
        original = runner.StatusWriter.running

        def running_then_stopper(self, *a, **k):
            ok = original(self, *a, **k)
            table.row["stopRequestedAt"] = "2026-11-07T08:00:00Z"   # backend request_stop
            return ok
        monkeypatch.setattr(runner.StatusWriter, "running", running_then_stopper)
        assert job.run() == {"status": "stopped"}
        assert s3.parts_uploaded == []
        assert table.row["status"] == "RUNNING", "the stopper writes the outcome"
        assert _open_uploads(s3) == []
        assert (AUX, KEY) not in s3.completed
