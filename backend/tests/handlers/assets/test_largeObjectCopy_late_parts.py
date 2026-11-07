# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""#1123 in-dev D5 and D6: a stopped copy leaves no part behind, and the cancel path signs its abort
for the destination bucket's Region.

D5. The fresh smoke on demo21 cancelled or stopped three copies (the Executions UI abort, a console
stop of the copy execution, a console stop of the workflow execution). Each time the multipart upload
stayed listed with its parts (7, 13 and 18 GiB) minutes later. StopExecution does not stop a CopyBatch
task already running, and S3 keeps a part whose UploadPartCopy was in flight during an abort ("you
may need to abort more than once"). The cancel path and the engine aborted once, and the CopyBatch
tasks kept copying into the upload.

Now a stopper records its stop on the job row (stopRequestedAt) before it aborts; each CopyBatch task
reads the job before and after every part, copies no further part once it sees the stop or a
finalized job, and aborts the upload again once its own copies have ended; the engine's reconcile and
its failure cleanup abort until ListParts answers NoSuchUpload (bounded). The landed-object decision
is unchanged: a job whose upload completed keeps its object.

``_LateS3`` is moto with S3's documented abort behaviour: an abort sent while a part copy is in
flight answers 204 and does not remove the upload, and the part lands. moto alone removes the upload
at once, which hid D5. Each ordering ends with zero uploads, on a versioned asset bucket and on an
unversioned external bucket without a lifecycle rule.

D6. The abort route (executionService) holds AbortMultipartUpload but could not read the asset
buckets table, so the cancel path signed its abort for the deployment Region: an abort for a bucket
in another Region failed and kept the upload. The job row now carries destRegion, and the cancel
path registers it before the abort.

Every test fails on 3667d4da7."""

import sys
import threading
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from backend.tests.handlers.assets.test_largeObjectCopy_engine import (  # noqa: F401 (fixture)
    SMALL_PARTS, engine, env, loc)
from backend.tests.handlers.assets.test_largeObjectCopy_reconcile import (  # noqa: F401 (fixture)
    EXEC_ARN, _described, machine)

VERSIONED = "asset-bucket"
UNVERSIONED = "ext-bucket"  # an external bucket: no versioning, no VAMS lifecycle rule
BUCKETS = pytest.mark.parametrize("bucket", [VERSIONED, UNVERSIONED])
SOURCE_KEY = "pipelines/p/j/output/e1/files/big.bin"


class _LateS3:
    """moto's S3 with S3's abort semantics for part copies in flight.

    While ``in_flight`` is above zero an abort answers 204 and leaves the upload (and its parts) in
    place, as S3 does for a part upload still running; with no copy in flight it is moto's abort
    (the upload and its parts are gone; a repeat answers NoSuchUpload). ``during_part`` maps a part
    number to a callable run while that part's copy is in flight (the stopper)."""

    def __init__(self, s3):
        self._s3 = s3
        self.in_flight = 0
        self.during_part = {}
        self.aborts_in_flight = 0
        self._lock = threading.Lock()

    def abort_multipart_upload(self, **kwargs):
        with self._lock:
            busy = self.in_flight > 0
        if busy:
            self.aborts_in_flight += 1
            return {"ResponseMetadata": {"HTTPStatusCode": 204}}
        return self._s3.abort_multipart_upload(**kwargs)

    def upload_part_copy(self, **kwargs):
        with self._lock:
            self.in_flight += 1
        try:
            hook = self.during_part.pop(int(kwargs["PartNumber"]), None)
            if hook:
                hook()
            return self._s3.upload_part_copy(**kwargs)
        finally:
            with self._lock:
                self.in_flight -= 1

    def __getattr__(self, name):
        return getattr(self._s3, name)


def _one_thread():
    """One copy thread per batch, so the part order is deterministic."""
    return patch.object(loc, "settings", lambda environ=None: {**SMALL_PARTS, "threadsPerBatch": 1})


def _start(env, bucket):
    if bucket == UNVERSIONED:
        env["s3"].create_bucket(Bucket=UNVERSIONED)
    job = loc.build_job(
        kind=loc.KIND_WORKFLOW_OUTPUT,
        source={"bucket": "run-bucket", "key": SOURCE_KEY},
        dest={"bucket": bucket, "key": "a1/big.bin"},
        metadata={"databaseid": "db", "assetid": "a1"}, content_headers={}, database_id="db",
        asset_id="a1", upload_id="y1", upload_type="assetFile", relative_key="big.bin",
        change_user_id="u1", scope_id="e1", workflow_execution_id="e1", workflow_id="wf",
        workflow_database_id="GLOBAL", batch_id="e1")
    starter = MagicMock(**{"start_execution.return_value": {"executionArn": EXEC_ARN}})
    loc.start_copy_job(env["jobs"], starter, "arn:aws:states:r:1:stateMachine:sm", job)
    prepared = engine.prepare(job)
    return job, prepared


def _uploads(env, bucket):
    return env["s3"].list_multipart_uploads(Bucket=bucket).get("Uploads", [])


def _cancel(env, client):
    return loc.cancel_execution_copy_jobs(env["jobs"], env["sfn"], client, "e1")


def _row(env, job):
    return loc.get_job_row(env["jobs"], job["jobId"])


def _copy_all(prepared):
    """Every batch of the job, each as its own CopyBatch task; returns the refusals."""
    refusals = []
    for batch in prepared["batches"]:
        try:
            engine.copy_batch(prepared["job"], batch)
        except loc.CopyRefused as e:
            refusals.append(str(e))
    return refusals


def _reconcile_cancelled_stop(env, job):
    env["sfn"].describe_execution.return_value = _described(
        job, status="ABORTED", error=loc.STOP_ERROR_CANCELLED)
    return engine.reconcile(EXEC_ARN)


# --------------------------------------------------------------------------------------------- #
# D5: a stop while a part copy is in flight leaves no part
# --------------------------------------------------------------------------------------------- #

@BUCKETS
def test_a_cancel_during_a_part_copy_leaves_no_upload(env, machine, bucket):
    """The Executions UI abort (cancel path) lands while part 2 is being copied."""
    with _one_thread():
        job, prepared = _start(env, bucket)
        late = _LateS3(env["s3"])
        late.during_part[2] = lambda: _cancel(env, late)
        with patch.object(engine, "_client_for_bucket", lambda b: late):
            refusals = _copy_all(prepared)
            assert late.aborts_in_flight >= 1  # the cancel's abort did not remove the upload
            # The copy stopped: part 3 (the next batch) was never copied.
            assert refusals == [loc.REASON_CANCELLED, loc.REASON_CANCELLED]
            assert _row(env, job)["status"] == loc.STATUS_CANCELLED
            # Before: the upload stayed listed with parts 1-3.
            assert _uploads(env, bucket) == []
            _reconcile_cancelled_stop(env, job)
    assert _uploads(env, bucket) == []
    assert _row(env, job)["status"] == loc.STATUS_CANCELLED


@BUCKETS
def test_a_cancel_between_two_parts_copies_no_further_part(env, machine, bucket):
    """The cancel lands after part 1 returned and before part 2 starts: part 2 is not copied."""
    with _one_thread():
        job, prepared = _start(env, bucket)
        late = _LateS3(env["s3"])
        copied = []
        real_copy = engine._copy_part

        def copy_then_cancel(j, number):
            real_copy(j, number)
            copied.append(number)
            if number == 1:
                _cancel(env, late)

        with patch.object(engine, "_client_for_bucket", lambda b: late), \
                patch.object(engine, "_copy_part", copy_then_cancel):
            refusals = _copy_all(prepared)
    # Before: parts 2 and 3 were copied after the cancel.
    assert copied == [1]
    assert refusals == [loc.REASON_CANCELLED, loc.REASON_CANCELLED]
    assert _uploads(env, bucket) == []


@BUCKETS
def test_a_console_stop_during_a_part_copy_leaves_no_upload(env, machine, bucket):
    """A StopExecution outside VAMS: the status change rule's reconcile runs while part 2 is copied."""
    def console_stop_reconciled():
        env["sfn"].describe_execution.return_value = _described(job, status="ABORTED", error="")
        engine.reconcile(EXEC_ARN)

    with _one_thread(), patch.object(loc, "_sleep", lambda s: None):
        job, prepared = _start(env, bucket)
        late = _LateS3(env["s3"])
        late.during_part[2] = console_stop_reconciled
        with patch.object(engine, "_client_for_bucket", lambda b: late):
            refusals = _copy_all(prepared)
    assert _row(env, job)["status"] == loc.STATUS_FAILED
    assert refusals and set(refusals) <= {loc.REASON_CANCELLED, loc.REASON_COPY_FAILED}
    # Before: parts 1-3 stayed in the upload.
    assert _uploads(env, bucket) == []


@BUCKETS
def test_the_reconcile_aborts_again_while_a_copy_of_another_task_lands(env, machine, bucket):
    """The cancel path aborted once while a part copy of another CopyBatch task was in flight, and
    that task ended without its own abort (its invocation timed out). The engine's reconcile of the
    stop aborts until ListParts answers NoSuchUpload."""
    with _one_thread():
        job, prepared = _start(env, bucket)
        _copy_all({**prepared, "batches": prepared["batches"][:1]})  # parts 1-2 copied
        late = _LateS3(env["s3"])
        late.in_flight = 1  # part 3, in another task, is being copied
        assert _cancel(env, late) == []
        assert late.aborts_in_flight == 1
        assert _row(env, job)["status"] == loc.STATUS_CANCELLED
        upload_id = _row(env, job)["uploadIdS3"]

        def the_copy_ends(seconds):
            # While the reconcile waits, part 3 lands and its task ends without aborting.
            late.in_flight = 0
            source = prepared["job"]["source"]
            first, last = loc.part_range(3, prepared["job"]["partSize"], source["size"])
            env["s3"].upload_part_copy(
                Bucket=bucket, Key="a1/big.bin", UploadId=upload_id, PartNumber=3,
                CopySource={"Bucket": "run-bucket", "Key": SOURCE_KEY, "VersionId": source["versionId"]},
                CopySourceRange=f"bytes={first}-{last}")

        with patch.object(engine, "_client_for_bucket", lambda b: late), \
                patch.object(loc, "_sleep", the_copy_ends):
            _reconcile_cancelled_stop(env, job)
    # Before: the reconcile left a CANCELLED job alone and parts 1-3 stayed.
    assert _uploads(env, bucket) == []


@BUCKETS
def test_a_failed_copy_aborts_again_until_the_upload_is_gone(env, machine, bucket):
    """Fail (a batch refused, the Map stopped the others): a part of another batch still lands after
    Fail's first abort; Fail's cleanup aborts until the upload is gone."""
    with _one_thread():
        job, prepared = _start(env, bucket)
        _copy_all({**prepared, "batches": prepared["batches"][:1]})
        late = _LateS3(env["s3"])
        late.in_flight = 1
        waits = []

        def the_copy_ends(seconds):
            waits.append(seconds)
            late.in_flight = 0

        with patch.object(engine, "_client_for_bucket", lambda b: late), \
                patch.object(loc, "_sleep", the_copy_ends):
            result = engine.fail(prepared["job"], {"Error": "States.TaskFailed", "Cause": ""})
    assert result["job"]["error"]
    assert _row(env, job)["status"] == loc.STATUS_FAILED
    assert waits == [loc.ABORT_SWEEP_WAIT_SECONDS]
    # Before: one abort, answered 204, and the upload stayed.
    assert _uploads(env, bucket) == []


def test_the_abort_sweep_is_bounded(env):
    """An upload that keeps receiving parts is aborted at most ABORT_SWEEP_ATTEMPTS times."""
    late = _LateS3(env["s3"])
    late.in_flight = 1
    created = env["s3"].create_multipart_upload(Bucket=VERSIONED, Key="a1/x.bin")
    waits = []
    gone = loc.abort_until_gone(late, VERSIONED, "a1/x.bin", created["UploadId"],
                                sleep=waits.append)
    assert gone is False
    assert late.aborts_in_flight == loc.ABORT_SWEEP_ATTEMPTS
    assert len(waits) == loc.ABORT_SWEEP_ATTEMPTS - 1


def test_the_abort_sweep_raises_on_a_real_abort_error(env):
    client = MagicMock()
    client.abort_multipart_upload.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "AbortMultipartUpload")
    with pytest.raises(ClientError):
        loc.abort_until_gone(client, VERSIONED, "k", "u", sleep=lambda s: None)


def test_a_landed_upload_is_never_aborted_by_the_stop_paths(env, machine):
    """The landed-object rule is unchanged: Complete landed the file before the cancel; the cancel,
    the reconcile of its stop and a stale CopyBatch task leave the object in place."""
    with _one_thread():
        job, prepared = _start(env, VERSIONED)
        _copy_all(prepared)
        engine.complete(prepared["job"])
        landed = env["s3"].head_object(Bucket=VERSIONED, Key="a1/big.bin")
        assert _cancel(env, env["s3"]) == []
        _reconcile_cancelled_stop(env, job)
        with pytest.raises(loc.CopyRefused):
            engine.copy_batch(prepared["job"], prepared["batches"][0])
    row = _row(env, job)
    assert row["status"] == loc.STATUS_SUCCEEDED
    now = env["s3"].head_object(Bucket=VERSIONED, Key="a1/big.bin")
    assert now["VersionId"] == landed["VersionId"] and now["ETag"] == landed["ETag"]


def test_the_stop_marker_changes_no_status(env, machine):
    with _one_thread():
        job, _prepared = _start(env, VERSIONED)
    assert loc.request_stop(env["jobs"], job["jobId"]) is True
    row = _row(env, job)
    assert row["status"] == loc.STATUS_RUNNING and row["stopRequestedAt"]
    assert loc.copy_should_stop(row) is True
    loc.mark_cancelled(env["jobs"], job["jobId"])
    # Not written over a finalized job.
    assert loc.request_stop(env["jobs"], job["jobId"]) is False


# --------------------------------------------------------------------------------------------- #
# D6: the cancel path signs its abort for the destination bucket's Region
# --------------------------------------------------------------------------------------------- #

class _RegionRecorder:
    def __init__(self, region, s3):
        self.region = region
        self._s3 = s3
        self.calls = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append(name)
            return getattr(self._s3, name)(*args, **kwargs)
        return call


def test_the_job_row_carries_the_destination_region(env):
    job = loc.build_job(
        kind=loc.KIND_UPLOAD, source={"bucket": "run-bucket", "key": SOURCE_KEY},
        dest={"bucket": "far-bucket", "key": "a1/big.bin", "region": "eu-west-1"}, metadata={},
        content_headers={}, database_id="db", asset_id="a1", upload_id="y1", upload_type="assetFile",
        relative_key="big.bin", change_user_id="u1", scope_id="y1")
    starter = MagicMock(**{"start_execution.return_value": {"executionArn": EXEC_ARN}})
    loc.start_copy_job(env["jobs"], starter, "arn:aws:states:r:1:stateMachine:sm", job)
    assert loc.get_job_row(env["jobs"], job["jobId"])["destRegion"] == "eu-west-1"


def test_the_cancel_path_aborts_in_the_destination_buckets_region(env, machine):
    """A cross-Region external bucket: the abort and the landed check go to the bucket's Region
    without a buckets table read (the abort route's role cannot read that table)."""
    s3mod = sys.modules["common.s3"]
    with _one_thread():
        job, prepared = _start(env, VERSIONED)
    # The job's destination is a bucket in eu-west-1 (as start_copy_job records it from the job).
    loc.update_job_row(env["jobs"], job["jobId"], {"destRegion": "eu-west-1"})
    s3mod._bucket_regions_by_name.pop(VERSIONED, None)
    recorders = {}

    def client_for_region(region=None):
        region = region or s3mod.deployment_region()
        return recorders.setdefault(region, _RegionRecorder(region, env["s3"]))

    try:
        with patch.object(s3mod, "s3_client_for_region", client_for_region), \
                patch.object(s3mod, "boto3", MagicMock(**{"client.return_value":
                                                         _RegionRecorder("default", env["s3"])})):
            router = s3mod.region_routing_s3_client()
            assert _cancel(env, router) == []
        # Before: signed for the deployment Region (the default client), so a bucket in another
        # Region refused it and the upload stayed.
        assert "abort_multipart_upload" in recorders["eu-west-1"].calls
        assert "abort_multipart_upload" not in router._default_client.calls
    finally:
        s3mod._bucket_regions_by_name.pop(VERSIONED, None)
    assert _uploads(env, VERSIONED) == []
