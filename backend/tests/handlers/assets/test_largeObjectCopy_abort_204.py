# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""#1123 in-dev D1: S3 answers 204 to an abort of an upload a Complete completed, and the object stays.

The live S3 probe on the asset bucket (versioned, KMS) showed that AbortMultipartUpload sent while a
CompleteMultipartUpload is in flight, or 0, 1, 5 or 30 seconds after it returned, answers 204 (not
NoSuchUpload), and the completed object stays at the key. moto answers NoSuchUpload there, so every
earlier test took the "upload already gone" branch, while the cancel path, the reconcile of a stop
the cancel path made and the reconcile of a console stop all read a 204 as "nothing landed": the job
was CANCELLED or FAILED while its object was the file's current version, with no version id, no
completion audit entry, no staged-source delete and no email.

Now each caller decides by the job's own landed object (its upload token) whatever the abort
answered, and a Complete in flight that lands after a remover decided supersedes that outcome once
(its own CompleteMultipartUpload response names the object). Every test fails on 5709b3642."""

from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError

from backend.tests.handlers.assets.test_largeObjectCopy_engine import (  # noqa: F401 (fixture)
    _uploads_table, engine, env, loc)
from backend.tests.handlers.assets.test_largeObjectCopy_reconcile import (  # noqa: F401 (fixture)
    EXEC_ARN, _described, machine)
from backend.tests.handlers.assets.test_largeObjectCopy_race_prior_object import (
    UNVERSIONED, VERSIONED, _Run)

BUCKETS = pytest.mark.parametrize("bucket", [VERSIONED, UNVERSIONED])


class _S3Abort204:
    """An S3 client that answers AbortMultipartUpload as S3 does.

    completed: the upload was completed (or a Complete is completing it): the abort answers 204 and
    the object stays. moto would raise NoSuchUpload, the branch every earlier test took.
    in_flight: a Complete is running: the abort answers 204 and does not stop it (S3 does not refuse
    an abort sent during a Complete; the Complete can still succeed)."""

    def __init__(self, s3, in_flight=False):
        self._s3 = s3
        self._in_flight = in_flight
        self.aborts = 0

    def abort_multipart_upload(self, **kwargs):
        self.aborts += 1
        if self._in_flight:
            return {"ResponseMetadata": {"HTTPStatusCode": 204}}
        try:
            return self._s3.abort_multipart_upload(**kwargs)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") != "NoSuchUpload":
                raise
            return {"ResponseMetadata": {"HTTPStatusCode": 204}}

    def __getattr__(self, name):
        return getattr(self._s3, name)


def _engine_client(client):
    return patch.object(engine, "_client_for_bucket", lambda bucket: client)


def _stop_reconciled(run):
    """The engine's reconcile of the stop (the cancel path's hand-over or its status change event)."""
    run.env["sfn"].describe_execution.return_value = _described(
        run.job, status="ABORTED", error=loc.STOP_ERROR_CANCELLED)
    return engine.reconcile(EXEC_ARN)


# --------------------------------------------------------------------------------------------- #
# A 204 abort of an already-completed upload, in every caller
# --------------------------------------------------------------------------------------------- #

@BUCKETS
def test_the_cancel_path_keeps_a_completed_upload_after_a_204_abort(env, machine, bucket):
    run = _Run(env, bucket)
    run.complete_by_hand()
    client = _S3Abort204(env["s3"])
    assert run.cancel(s3_client=client) == []
    assert client.aborts == 1
    # Before: CANCELLED while the new object was the asset's current version.
    assert run.row()["status"] == loc.STATUS_SUCCEEDED
    # The post-landing work is the engine's reconcile of the stop made here.
    with _engine_client(client):
        _stop_reconciled(run)
    run.assert_landed()


@BUCKETS
def test_the_reconcile_of_a_cancel_keeps_a_completed_upload_after_a_204_abort(env, machine, bucket):
    """The cancel path could not read the object and left the job to the engine."""
    run = _Run(env, bucket)
    run.complete_by_hand()
    with _engine_client(_S3Abort204(env["s3"])):
        result = _stop_reconciled(run)
    # Before: CANCELLED by the reconcile, nothing finished.
    assert result["status"] == loc.STATUS_SUCCEEDED
    run.assert_landed()


@BUCKETS
def test_the_reconcile_of_a_console_stop_keeps_a_completed_upload_after_a_204_abort(env, machine, bucket):
    run = _Run(env, bucket)
    run.complete_by_hand()
    run.console_stop()
    with _engine_client(_S3Abort204(env["s3"])):
        result = engine.reconcile(EXEC_ARN)
    # Before: FAILED "copy failed" with the new object at the key.
    assert result["status"] == loc.STATUS_SUCCEEDED
    run.assert_landed()
    assert run.row()["reconciledStatus"] == "ABORTED"



# --------------------------------------------------------------------------------------------- #
# A Complete in flight when the remover decided: the landed object wins
# --------------------------------------------------------------------------------------------- #

def _as_s3(completed):
    """moto names an unversioned bucket's object version "null"; S3 returns no VersionId there."""
    if completed.get("VersionId") == "null":
        completed = {k: v for k, v in completed.items() if k != "VersionId"}
    return completed


def _complete_with_remover_first(run, remover):
    """Complete, with ``remover`` run while its CompleteMultipartUpload is in flight (the abort
    answers 204 and does not stop it; the remover's landed check runs before the object exists)."""
    real = run.env["s3"].complete_multipart_upload

    def in_flight(**kwargs):
        remover()
        return _as_s3(real(**kwargs))

    with patch.object(run.env["s3"], "complete_multipart_upload", side_effect=in_flight):
        return engine.complete(run.pj)


@BUCKETS
def test_a_cancel_during_complete_is_superseded_by_the_landed_object(env, machine, bucket):
    run = _Run(env, bucket)
    client = _S3Abort204(env["s3"], in_flight=True)
    with _engine_client(client):
        result = _complete_with_remover_first(run, lambda: run.cancel(s3_client=client))
    row = run.row()
    # Before: CopyRefused, the job CANCELLED with its object the asset's current version.
    assert row["supersededStatus"] == loc.STATUS_CANCELLED
    assert "error" not in row
    assert result["job"]["destVersionId"] == row["destVersionId"]
    run.assert_landed(counted=False)


@BUCKETS
def test_a_console_stop_reconciled_during_complete_is_superseded(env, machine, bucket):
    run = _Run(env, bucket)
    run.console_stop()
    with _engine_client(_S3Abort204(env["s3"], in_flight=True)):
        _complete_with_remover_first(run, lambda: engine.reconcile(EXEC_ARN))
    row = run.row()
    assert row["supersededStatus"] == loc.STATUS_FAILED
    assert "error" not in row
    # The rejection entry the reconcile wrote stays; the completion entry follows it, once.
    assert run.audits() == [True, False]
    assert row["destVersionId"] == (run.head().get("VersionId") or "")
    assert run.holds_new_object()
    assert not run.staged_kept()
    env["lambda"].invoke.assert_called_once()


@BUCKETS
def test_a_superseded_job_is_finished_once(env, machine, bucket):
    """A retried Complete (its result lost) and a later reconcile change nothing more."""
    run = _Run(env, bucket)
    client = _S3Abort204(env["s3"], in_flight=True)
    with _engine_client(client):
        _complete_with_remover_first(run, lambda: run.cancel(s3_client=client))
        engine.complete(run.pj)
        _stop_reconciled(run)
    run.assert_landed()
    assert run.row()["supersededStatus"] == loc.STATUS_CANCELLED


def test_an_api_upload_failed_by_a_remover_records_the_landing(env, machine):
    """The upload record's FAILED outcome for the file is replaced by SUCCEEDED once it landed."""
    from backend.tests.handlers.assets.test_largeObjectCopy_engine import _job
    uploads = _uploads_table()
    with patch.object(engine, "upload_table", uploads):
        job = _job(kind=loc.KIND_UPLOAD)
        loc.update_job_row(env["jobs"], job["jobId"], {"executionArn": EXEC_ARN})
        prepared = engine.prepare(job)
        for batch in prepared["batches"]:
            engine.copy_batch(prepared["job"], batch)
        env["sfn"].describe_execution.return_value = _described(job, status="ABORTED", error="")
        real = env["s3"].complete_multipart_upload

        def in_flight(**kwargs):
            engine.reconcile(EXEC_ARN)
            return _as_s3(real(**kwargs))

        with _engine_client(_S3Abort204(env["s3"], in_flight=True)), \
                patch.object(env["s3"], "complete_multipart_upload", side_effect=in_flight):
            engine.complete(prepared["job"])
    row = loc.get_job_row(env["jobs"], job["jobId"])
    assert row["status"] == loc.STATUS_SUCCEEDED and row["supersededStatus"] == loc.STATUS_FAILED
    record = uploads.get_item(Key={"uploadId": "y1", "assetId": "a1"})["Item"]
    assert record["asyncFiles"]["big.bin"]["status"] == "SUCCEEDED"
    assert record["status"] != "COMPLETED_WITH_ERRORS"
