# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""#1125 D11: progress moves while a large member streams.

Live, a 1 TiB single-file job showed "Preparing 0%" for its whole 89 minutes: the container reported
progress only after each whole member. bytesDone now moves inside a member, every
PROGRESS_EVERY_BYTES bytes or PROGRESS_EVERY_SECONDS seconds, and the web and CLI progress (both read
bytesDone / bytesTotal) follow it."""

import functools
import io
import zipfile

import pytest

from zip_archive_pipeline import runner
from zip_archive_pipeline.engine.reader import PrefetchReader

from test_runner import ASSETS, BASE, FakeS3, OneClient, _manifest, _zip_job

MIB = 1024 * 1024


@pytest.fixture
def small_ranges(monkeypatch):
    """1 MiB ranges, so a few-MiB member streams as several chunks (the default is 32 MiB)."""
    monkeypatch.setattr(runner, "PrefetchReader", functools.partial(PrefetchReader, range_size=MIB))


def _big_member(s3, size=8 * MIB):
    s3.add(BASE + "big.bin", b"B" * size)
    return [{"bucket": ASSETS, "key": BASE + "big.bin", "versionId": "v1", "size": size,
             "relativePath": "/big.bin"}]


class _Sink:
    def __init__(self):
        self.buf = io.BytesIO()

    def write(self, data):
        self.buf.write(data)

    def tell(self):
        return self.buf.tell()


def _write(s3, members, monkeypatch, every_bytes, clock=None):
    monkeypatch.setattr(runner, "PROGRESS_EVERY_BYTES", every_bytes)
    calls = []
    kwargs = {"clock": clock} if clock else {}
    runner.write_archive(OneClient(s3), members, [], _Sink(), "zip", "store",
                         lambda files, size: calls.append((files, size)), **kwargs)
    return calls


def test_bytes_move_inside_a_single_large_member(small_ranges, monkeypatch):
    s3 = FakeS3()
    members = _big_member(s3)
    calls = _write(s3, members, monkeypatch, every_bytes=2 * MIB)
    inside = [c for c in calls if c[0] == 0]
    # Reported while the one member was still streaming: no file finished, bytes rising.
    assert [size for _, size in inside] == [2 * MIB, 4 * MIB, 6 * MIB, 8 * MIB]
    assert calls[-1][1] == 8 * MIB


def test_time_alone_triggers_a_report_on_a_slow_member(small_ranges, monkeypatch):
    s3 = FakeS3()
    members = _big_member(s3, size=3 * MIB)
    ticks = iter(range(0, 10000, 31))   # every clock read is 31 s after the last

    calls = _write(s3, members, monkeypatch, every_bytes=10 ** 15, clock=lambda: next(ticks))
    inside = [size for files, size in calls if files == 0]
    assert inside and inside[0] == MIB


def test_no_report_inside_a_member_below_both_thresholds(small_ranges, monkeypatch):
    s3 = FakeS3()
    members = _big_member(s3, size=3 * MIB)
    calls = _write(s3, members, monkeypatch, every_bytes=10 ** 15, clock=lambda: 0)
    assert [c for c in calls if c[0] == 0] == []


def test_the_job_row_shows_bytes_done_before_the_first_file_finishes(small_ranges, monkeypatch):
    s3 = FakeS3()
    _big_member(s3)
    monkeypatch.setattr(runner, "PROGRESS_EVERY_BYTES", 2 * MIB)
    job, table = _zip_job(s3, _manifest(entries=[{"type": "file", "key": BASE + "big.bin",
                                                  "relativePath": "/big.bin", "versionId": "v1"}]))
    assert job.run()["status"] == "succeeded"
    progress = [values for expr, _cond, values in table.updates if "bytesDone = :bd" in expr
                and "SUCCEEDED" not in values.values()]
    assert progress, "no progress write"
    assert progress[0][":fd"] == 0 and 0 < progress[0][":bd"] < 8 * MIB
    with zipfile.ZipFile(io.BytesIO(next(iter(s3.completed.values())))) as z:
        assert z.read("big.bin") == b"B" * 8 * MIB


def test_a_lost_row_during_a_member_stops_the_job(small_ranges, monkeypatch):
    """A progress write inside a member that loses (the user cancelled) stops the stream there."""
    s3 = FakeS3()
    _big_member(s3)
    monkeypatch.setattr(runner, "PROGRESS_EVERY_BYTES", 2 * MIB)
    job, table = _zip_job(s3, _manifest(entries=[{"type": "file", "key": BASE + "big.bin",
                                                  "relativePath": "/big.bin", "versionId": "v1"}]))
    original = table.update_item

    def cancel_on_first_progress(**kwargs):
        if "bytesDone = :bd" in kwargs["UpdateExpression"] and table.row["status"] == "RUNNING":
            table.row["status"] = "CANCELLED"
        return original(**kwargs)
    table.update_item = cancel_on_first_progress
    with pytest.raises(runner.ArchiveFailure):
        job.run()
    assert s3.completed == {}
