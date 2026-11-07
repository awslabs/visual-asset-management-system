#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A zip download job's status is monotonic and a landed archive wins with one outcome.

Drives the REAL container job (zip_archive_pipeline.runner.Job) and the REAL API-side finalizers
(backend common/zipDownloads.py: finalize_ended_run, which the terminal callback and the read-time
reconcile call, and cancel_job, which the user's cancel calls) against one S3 double and one job-row
double, and interleaves them at each point that matters:

- before the archive upload exists, while it is open, and after it completed but before the
  container recorded SUCCEEDED;
- the user's cancel, and a run the terminal callback or the reconcile sees FAILED / TIMED_OUT /
  ABORTED while its container is still writing (StopExecution and a Batch terminate do not stop it).

Every case ends with ONE outcome: the row's terminal status, and an archive object present exactly
when that status is SUCCEEDED.
"""

import importlib
import json
import os
import sys

import pytest
from botocore.exceptions import ClientError

from zip_archive_pipeline import runner
from test_runner import ASSETS, AUX, BASE, FakeDynamo, FakeS3, OneClient, _err

pytestmark = pytest.mark.unit

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), *[".."] * 5, "backend", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
zd = importlib.import_module("common.zipDownloads")

USER, REQUEST, EXECUTION = "alice", "r1", "exec-1"
PREFIX = zd.request_prefix("db1", "asset1", USER, REQUEST)
ARCHIVE_KEY = zd.archive_key("db1", "asset1", USER, REQUEST, "out.zip")
MANIFEST_KEY = zd.manifest_key("db1", "asset1", USER, REQUEST)


class SharedTable:
    """One job row, evaluating the conditions both the backend and the container write with."""

    def __init__(self, row):
        self.row = row
        self.outcomes = []   # every terminal status a write recorded

    def get_item(self, Key, ConsistentRead=False):
        return {"Item": dict(self.row)} if self.row is not None else {}

    def update_item(self, Key, UpdateExpression, ConditionExpression, ExpressionAttributeNames,
                    ExpressionAttributeValues):
        values, row = ExpressionAttributeValues, self.row
        ok = row is not None and row["status"] in (values[":queued"], values[":running"])
        if ok and "workflowExecutionId = :eid" in ConditionExpression:
            if "attribute_not_exists(workflowExecutionId) OR" in ConditionExpression:
                ok = "workflowExecutionId" not in row or row["workflowExecutionId"] == values[":eid"]
            else:
                ok = row.get("workflowExecutionId") == values[":eid"]
        if not ok:
            raise _err("ConditionalCheckFailedException")
        for assignment in UpdateExpression.replace("SET ", "", 1).split(", "):
            left, right = [p.strip() for p in assignment.split("=")]
            row[ExpressionAttributeNames.get(left, left)] = values[right]
        if row["status"] in zd.TERMINAL_STATUSES:
            self.outcomes.append(row["status"])


def _setup():
    s3 = FakeS3()
    s3.add(BASE + "a.glb", b"A" * 100)
    s3.add(BASE + "b.glb", b"B" * 50)
    manifest = {"schemaVersion": 1, "mode": "zipDownload", "requestId": REQUEST, "userId": USER,
                "databaseId": "db1", "assetId": "asset1", "assetBucket": ASSETS, "assetBaseKey": BASE,
                "archiveBucket": AUX, "archiveKey": ARCHIVE_KEY,
                "resolvedManifestKey": zd.resolved_manifest_key("db1", "asset1", USER, REQUEST),
                "archiveFormat": "zip", "compression": "store",
                "entries": [{"type": "asset", "key": BASE, "relativePath": "/"}], "pinnedFiles": [],
                "limits": {"maxFiles": 100, "maxTotalBytes": 10 ** 9}}
    s3.add(MANIFEST_KEY, json.dumps(manifest).encode(), bucket=AUX)
    table = SharedTable({"userId": USER, "requestId": REQUEST, "databaseId": "db1", "assetId": "asset1",
                         "status": "QUEUED", "workflowExecutionId": EXECUTION, "archiveKey": ARCHIVE_KEY})
    definition = {"mode": "zipDownload", "workflowExecutionId": EXECUTION,
                  "zipDownload": {"manifestBucket": AUX, "manifestKey": MANIFEST_KEY, "requestId": REQUEST,
                                  "userId": USER, "databaseId": "db1", "assetId": "asset1"}}
    job = runner.Job(definition, OneClient(s3), FakeDynamo(table), logger=lambda *a: None)
    return s3, table, job


def _own_archive_is_current(s3, table):
    """Whether the key's current object is the one this job's upload created (its token)."""
    try:
        head = s3.head_object(Bucket=AUX, Key=ARCHIVE_KEY)
    except ClientError:
        return False
    token = table.row.get("archiveUploadToken")
    return bool(token) and head["Metadata"].get(zd.ARCHIVE_UPLOAD_TOKEN_METADATA) == token


def _archive_bytes():
    """The size of the archive this setup produces, measured on a clean run."""
    s3, _, job = _setup()
    job.run()
    return len(s3.completed[(AUX, ARCHIVE_KEY)])


def _add_earlier_same_size_object(s3):
    s3.add(ARCHIVE_KEY, data=b"O" * _archive_bytes(), bucket=AUX, version="old",
           content_type="application/zip")


def _run(job):
    """Run the container; a failure it raises is part of the scenario, not of the test."""
    try:
        return job.run()
    except runner.ArchiveFailure as f:
        return {"status": "raised", "reason": f.reason}


def _assert_one_outcome(s3, table, expected):
    assert table.row["status"] == expected
    assert table.outcomes == [expected], "exactly one terminal write"
    assert _own_archive_is_current(s3, table) == (expected == zd.STATUS_SUCCEEDED)
    assert not [k for (b, k) in s3.uploads if b == AUX], "no upload left open"


def _hook(monkeypatch, where, action):
    """Run ``action`` at one point of the container's archive upload."""
    if where == "before_upload":
        class Sink(runner.MultipartSink):
            def __init__(self, *a, **k):
                action()
                super().__init__(*a, **k)
        monkeypatch.setattr(runner, "MultipartSink", Sink)
    elif where == "upload_open":
        class Sink(runner.MultipartSink):
            def close(self):
                action()
                return super().close()
        monkeypatch.setattr(runner, "MultipartSink", Sink)
    elif where == "landed_unrecorded":
        original = runner.StatusWriter.succeeded

        def succeeded(self, *a, **k):
            action()
            return original(self, *a, **k)
        monkeypatch.setattr(runner.StatusWriter, "succeeded", succeeded)
    else:
        raise AssertionError(where)


def _reconcile(s3, table, execution_status):
    return lambda: zd.finalize_ended_run(table, s3, AUX, dict(table.row), execution_status, "")


def _cancel(s3, table, results):
    return lambda: results.append(zd.cancel_job(table, s3, AUX, dict(table.row), zd.CANCELLED_BY_USER))


class TestCancelVersusArchiveCompletion:
    def test_cancel_while_the_upload_is_open_wins_and_nothing_lands(self, monkeypatch):
        s3, table, job = _setup()
        results = []
        _hook(monkeypatch, "upload_open", _cancel(s3, table, results))
        # The last part reads the job first, finds it CANCELLED and is never uploaded (#1125 D16).
        assert _run(job)["status"] == "stopped"
        assert results == [zd.CANCEL_CANCELLED]
        _assert_one_outcome(s3, table, zd.STATUS_CANCELLED)
        assert table.row["cancelledBy"] == zd.CANCELLED_BY_USER
        assert not getattr(s3, "deleted", []), "the archive never landed"

    def test_archive_landed_before_the_cancel_wins(self, monkeypatch):
        s3, table, job = _setup()
        results = []
        _hook(monkeypatch, "landed_unrecorded", _cancel(s3, table, results))
        assert _run(job)["status"] == "stopped"   # the container's SUCCEEDED write lost, harmlessly
        assert results == [zd.CANCEL_LANDED]
        _assert_one_outcome(s3, table, zd.STATUS_SUCCEEDED)
        assert table.row["archiveSizeBytes"] == len(s3.completed[(AUX, ARCHIVE_KEY)])
        assert not getattr(s3, "deleted", []), "the landed archive is kept"

    def test_cancel_before_the_upload_exists_uploads_no_part(self, monkeypatch):
        s3, table, job = _setup()
        results = []
        _hook(monkeypatch, "before_upload", _cancel(s3, table, results))
        _run(job)
        assert results == [zd.CANCEL_CANCELLED]
        _assert_one_outcome(s3, table, zd.STATUS_CANCELLED)

    def test_container_success_first_then_cancel_has_nothing_to_do(self):
        s3, table, job = _setup()
        assert job.run()["status"] == "succeeded"
        # delete_job only cancels an active row; the reconcile of a late abort leaves it alone.
        assert zd.finalize_ended_run(table, s3, AUX, dict(table.row), "ABORTED") == (None, False)
        _assert_one_outcome(s3, table, zd.STATUS_SUCCEEDED)


@pytest.mark.parametrize("execution_status", ["FAILED", "TIMED_OUT", "ABORTED"])
class TestReconcileFailedVersusLateContainerSuccess:
    def test_reconcile_while_the_upload_is_open_wins_and_nothing_lands(self, monkeypatch, execution_status):
        s3, table, job = _setup()
        _hook(monkeypatch, "upload_open", _reconcile(s3, table, execution_status))
        assert _run(job)["status"] == "stopped"   # the last part found the job FAILED (#1125 D16)
        _assert_one_outcome(s3, table, zd.STATUS_FAILED)
        assert not getattr(s3, "deleted", []), "the archive never landed"

    def test_archive_landed_before_the_reconcile_wins(self, monkeypatch, execution_status):
        s3, table, job = _setup()
        _hook(monkeypatch, "landed_unrecorded", _reconcile(s3, table, execution_status))
        assert _run(job)["status"] == "stopped"
        _assert_one_outcome(s3, table, zd.STATUS_SUCCEEDED)
        assert not getattr(s3, "deleted", [])

    def test_reconcile_before_the_upload_exists_uploads_no_part(self, monkeypatch, execution_status):
        s3, table, job = _setup()
        _hook(monkeypatch, "before_upload", _reconcile(s3, table, execution_status))
        _run(job)
        _assert_one_outcome(s3, table, zd.STATUS_FAILED)

    def test_container_success_first_then_a_late_reconcile_changes_nothing(self, execution_status):
        s3, table, job = _setup()
        assert job.run()["status"] == "succeeded"
        assert zd.finalize_ended_run(table, s3, AUX, dict(table.row), execution_status) == (None, False)
        _assert_one_outcome(s3, table, zd.STATUS_SUCCEEDED)


class TestContainerNeverFailsALandedArchive:
    def test_transient_error_on_the_success_write_leaves_it_to_the_callback(self, monkeypatch):
        s3, table, job = _setup()

        def throttled(self, *a, **k):
            raise _err("ProvisionedThroughputExceededException")
        monkeypatch.setattr(runner.StatusWriter, "succeeded", throttled)
        assert _run(job)["status"] == "raised"
        assert table.row["status"] == "RUNNING", "no FAILED over a landed archive"
        # The run ends FAILED (the container exited non-zero); the terminal callback settles it.
        assert zd.finalize_ended_run(table, s3, AUX, dict(table.row), "FAILED")[0] == zd.STATUS_SUCCEEDED
        _assert_one_outcome(s3, table, zd.STATUS_SUCCEEDED)

    def test_completion_that_landed_but_answered_an_error(self, monkeypatch):
        s3, table, job = _setup()
        original = FakeS3.complete_multipart_upload

        def landed_then_error(self, **kw):
            original(self, **kw)
            raise _err("InternalError")
        monkeypatch.setattr(FakeS3, "complete_multipart_upload", landed_then_error)
        assert _run(job)["status"] == "raised"
        assert table.row["status"] == "RUNNING", "the HEAD found the archive: no FAILED write"
        assert zd.finalize_ended_run(table, s3, AUX, dict(table.row), "FAILED")[0] == zd.STATUS_SUCCEEDED
        _assert_one_outcome(s3, table, zd.STATUS_SUCCEEDED)

    def test_sigterm_after_the_archive_landed_writes_nothing(self, monkeypatch):
        s3, table, job = _setup()

        def terminated(self, *a, **k):
            job.stop()   # what the SIGTERM handler does, before it raises SystemExit
            raise SystemExit(143)
        monkeypatch.setattr(runner.StatusWriter, "succeeded", terminated)
        with pytest.raises(SystemExit):
            job.run()
        assert table.row["status"] == "RUNNING"
        assert zd.finalize_ended_run(table, s3, AUX, dict(table.row), "ABORTED")[0] == zd.STATUS_SUCCEEDED
        _assert_one_outcome(s3, table, zd.STATUS_SUCCEEDED)

    def test_sigterm_while_the_upload_is_open_records_the_stop(self, monkeypatch):
        s3, table, job = _setup()
        _hook(monkeypatch, "upload_open", job.stop)
        assert _run(job)["status"] == "raised"
        _assert_one_outcome(s3, table, zd.STATUS_FAILED)
        assert table.row["errorMessage"] == runner.STOPPED

    def test_a_job_ended_before_the_upload_lands_no_archive(self, monkeypatch):
        # Before #1125 D16 the container uploaded every part, completed, lost its SUCCEEDED write and
        # deleted the late archive. The first part now reads the job, finds it FAILED and uploads
        # nothing. A Complete already in flight when the job ends still lands and is deleted by
        # version (test_abort_204.TestDeciderDuringTheComplete).
        s3, table, job = _setup()
        _hook(monkeypatch, "before_upload", _reconcile(s3, table, "FAILED"))
        assert _run(job)["status"] == "stopped"
        assert (AUX, ARCHIVE_KEY) not in s3.completed
        assert not getattr(s3, "deleted", [])
        _assert_one_outcome(s3, table, zd.STATUS_FAILED)

    def test_decider_holding_a_row_read_before_the_token_still_sees_the_landing(self, monkeypatch):
        # The reconcile read the row (QUEUED, no token) before the container started; the archive
        # then landed unrecorded. The decision re-reads the row after the abort, so it wins.
        s3, table, job = _setup()
        stale = dict(table.row)
        _hook(monkeypatch, "landed_unrecorded",
              lambda: zd.finalize_ended_run(table, s3, AUX, stale, "FAILED"))
        assert _run(job)["status"] == "stopped"
        _assert_one_outcome(s3, table, zd.STATUS_SUCCEEDED)

    def test_upload_token_key_matches_the_backend(self):
        keys = importlib.import_module("common.s3MetadataKeys")
        assert runner.ARCHIVE_UPLOAD_TOKEN_METADATA == zd.ARCHIVE_UPLOAD_TOKEN_METADATA
        # One upload token key for the zip archive and the large object copy engine.
        assert runner.ARCHIVE_UPLOAD_TOKEN_METADATA == keys.VAMS_COPY_UPLOAD_TOKEN_METADATA_KEY


class TestEarlierSameSizeObjectAtTheKey:
    """'Landed' is the identity of THIS job's upload (its token or version id), never presence or
    size: an earlier object of exactly the archive's size at the key must not count."""

    def test_reconcile_while_the_upload_is_open_fails_the_job(self, monkeypatch):
        s3, table, job = _setup()
        _add_earlier_same_size_object(s3)
        _hook(monkeypatch, "upload_open", _reconcile(s3, table, "FAILED"))
        assert _run(job)["status"] == "stopped"
        _assert_one_outcome(s3, table, zd.STATUS_FAILED)
        assert s3.head_object(Bucket=AUX, Key=ARCHIVE_KEY)["VersionId"] == "old"

    def test_cancel_while_the_upload_is_open_cancels_the_job(self, monkeypatch):
        s3, table, job = _setup()
        _add_earlier_same_size_object(s3)
        results = []
        _hook(monkeypatch, "upload_open", _cancel(s3, table, results))
        _run(job)
        assert results == [zd.CANCEL_CANCELLED]
        _assert_one_outcome(s3, table, zd.STATUS_CANCELLED)

    def test_succeeded_run_without_its_own_archive_fails(self):
        s3, table, _ = _setup()
        _add_earlier_same_size_object(s3)
        table.row.update(status="RUNNING", archiveUploadToken="token-of-this-job")
        assert zd.finalize_ended_run(table, s3, AUX, dict(table.row), "SUCCEEDED")[0] == zd.STATUS_FAILED
        assert table.row["errorMessage"] == zd.MSG_NOT_PRODUCED

    def test_container_failure_is_recorded_despite_the_earlier_object(self, monkeypatch):
        s3, table, job = _setup()
        _add_earlier_same_size_object(s3)

        def broken(self):
            raise IOError("network down")
        monkeypatch.setattr(runner.MultipartSink, "close", broken)
        assert _run(job)["reason"] == runner.GENERIC_FAILURE
        _assert_one_outcome(s3, table, zd.STATUS_FAILED)
        assert table.row["errorMessage"] == runner.GENERIC_FAILURE

    def test_own_archive_landing_over_the_earlier_object_still_wins(self, monkeypatch):
        s3, table, job = _setup()
        _add_earlier_same_size_object(s3)
        _hook(monkeypatch, "landed_unrecorded", _reconcile(s3, table, "FAILED"))
        assert _run(job)["status"] == "stopped"
        _assert_one_outcome(s3, table, zd.STATUS_SUCCEEDED)

    def test_late_archive_over_the_earlier_object_is_removed_and_the_earlier_one_kept(self, monkeypatch):
        s3, table, job = _setup()
        _add_earlier_same_size_object(s3)
        _hook(monkeypatch, "before_upload", _reconcile(s3, table, "FAILED"))
        _run(job)
        _assert_one_outcome(s3, table, zd.STATUS_FAILED)
        assert s3.head_object(Bucket=AUX, Key=ARCHIVE_KEY)["VersionId"] == "old"
