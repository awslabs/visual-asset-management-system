# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Round 4 of #1123 (M10, L13, D9): "a landed object wins" is decided ONLY by the object this job's
own upload created, never by size or presence.

The destination key already holds an earlier object of the same size and part count (an earlier run
of the same output), and the new run's staged output has different bytes. A console stop (the
reconcile) or the cancel path aborts the new run's multipart upload before, during, or after its
Complete. Before this change, Complete's own NoSuchUpload branch accepted any object of the planned
size and part count, so an abort during Complete finalized the job SUCCEEDED on the OLD object, wrote
a completion audit entry and deleted the only copy of the new output.

Now Prepare records a fresh destUploadToken on the job row in the same conditional write as the
upload id and stamps it on CreateMultipartUpload as object metadata. Complete uses the version id
CompleteMultipartUpload returned; when it did not see that response (NoSuchUpload), it, the reconcile
and the cancel path count the key's current object as landed only when it carries the job's token.
Both a versioned asset bucket and an unversioned (external) bucket are covered."""

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from backend.tests.handlers.assets.test_largeObjectCopy_engine import (  # noqa: F401 (fixture)
    engine, env, loc)
from backend.tests.handlers.assets.test_largeObjectCopy_reconcile import (  # noqa: F401 (fixture)
    EXEC_ARN, _described, machine)

TOKEN_KEY = "vams-copyuploadtoken"
STAGED_KEY = "pipelines/p/j/output/e2/files/big.bin"
VERSIONED = "asset-bucket"
UNVERSIONED = "ext-bucket"


def _kwargs(bucket, scope):
    return dict(
        kind=loc.KIND_WORKFLOW_OUTPUT,
        source={"bucket": "run-bucket", "key": STAGED_KEY.replace("/e2/", f"/{scope}/")},
        dest={"bucket": bucket, "key": "a1/big.bin"},
        metadata={"databaseid": "db", "assetid": "a1"}, content_headers={}, database_id="db",
        asset_id="a1", upload_id="y1", upload_type="assetFile", relative_key="big.bin",
        change_user_id="u1", scope_id=scope, workflow_execution_id=scope, workflow_id="wf",
        workflow_database_id="GLOBAL", batch_id=scope)


class _Run:
    """An earlier run landed its object at the key; the second run (new bytes, same size) is
    prepared and fully copied, its Complete not run yet."""

    def __init__(self, env, bucket):
        self.env = env
        s3 = env["s3"]
        self.dest = {"Bucket": bucket, "Key": "a1/big.bin"}
        if bucket == UNVERSIONED:
            s3.create_bucket(Bucket=UNVERSIONED)
        body = s3.get_object(Bucket="run-bucket", Key="pipelines/p/j/output/e1/files/big.bin")["Body"].read()
        first = self._start("e1")
        s3.put_object(Bucket="run-bucket", Key=first["source"]["key"], Body=body)
        engine.complete(self._prepare_and_copy(first))
        self.old = s3.head_object(**self.dest)
        assert self.old["ContentLength"] == len(body) and self.old["ETag"].endswith('-3"')
        self.new_body = bytes(reversed(body))
        self.job = self._start("e2")
        s3.put_object(Bucket="run-bucket", Key=self.job["source"]["key"], Body=self.new_body)
        self.pj = self._prepare_and_copy(self.job)
        env["audit"].reset_mock()
        env["lambda"].reset_mock()
        env["sfn"].reset_mock()
        loc.update_job_row(env["jobs"], self.job["jobId"], {"executionArn": EXEC_ARN})

    def _start(self, scope):
        job = loc.build_job(**_kwargs(self.dest["Bucket"], scope))
        starter = MagicMock(**{"start_execution.return_value": {"executionArn": f"arn-{scope}"}})
        loc.start_copy_job(self.env["jobs"], starter, "arn:aws:states:r:1:stateMachine:sm", job)
        return job

    @staticmethod
    def _prepare_and_copy(job):
        prepared = engine.prepare(job)
        for batch in prepared["batches"]:
            engine.copy_batch(prepared["job"], batch)
        return prepared["job"]

    # The pieces of the race
    def abort(self):
        self.env["s3"].abort_multipart_upload(UploadId=self.pj["uploadIdS3"], **self.dest)

    def complete_by_hand(self):
        """What an in-flight Complete has done once CompleteMultipartUpload returned (its
        invocation then ended before its finalize, or is still running)."""
        s3 = self.env["s3"]
        parts = s3.list_parts(UploadId=self.pj["uploadIdS3"], **self.dest)["Parts"]
        s3.complete_multipart_upload(
            UploadId=self.pj["uploadIdS3"], **self.dest,
            MultipartUpload={"Parts": [{"PartNumber": p["PartNumber"], "ETag": p["ETag"]} for p in parts]})

    def console_stop(self):
        self.env["sfn"].describe_execution.return_value = _described(self.job, status="ABORTED", error="")

    def cancel(self, s3_client=None):
        self.env["sfn"].describe_execution.return_value = _described(
            self.job, status="ABORTED", error=loc.STOP_ERROR_CANCELLED)
        return loc.cancel_execution_copy_jobs(self.env["jobs"], self.env["sfn"],
                                              s3_client or self.env["s3"], "e2")

    # What each ordering ends with
    def row(self):
        return loc.get_job_row(self.env["jobs"], self.job["jobId"])

    def head(self):
        return self.env["s3"].head_object(**self.dest)

    def holds_old_object(self):
        now = self.head()
        return now["ETag"] == self.old["ETag"] and now.get("VersionId") == self.old.get("VersionId")

    def holds_new_object(self):
        s3 = self.env["s3"]
        return s3.get_object(**self.dest)["Body"].read() == self.new_body

    def staged_kept(self):
        listed = self.env["s3"].list_objects_v2(Bucket="run-bucket", Prefix=self.job["source"]["key"])
        return "Contents" in listed

    def audits(self):
        return [c.args[4] for c in self.env["audit"].call_args_list]

    def assert_not_landed(self, status):
        row = self.row()
        assert row["status"] == status
        assert "destVersionId" not in row
        assert self.holds_old_object()
        assert self.staged_kept()  # the only copy of the new output is not deleted
        assert False not in self.audits()  # no completion audit entry
        self.env["lambda"].invoke.assert_not_called()  # no subscription email
        assert self.env["s3"].list_multipart_uploads(Bucket=self.dest["Bucket"]).get("Uploads", []) == []

    def assert_landed(self, counted=True):
        row = self.row()
        assert row["status"] == loc.STATUS_SUCCEEDED
        assert self.holds_new_object() and not self.holds_old_object()
        assert row["destVersionId"] == (self.head().get("VersionId") or "")
        assert self.audits() == [False]  # exactly one completion entry
        self.env["lambda"].invoke.assert_called_once()  # one subscription email
        assert not self.staged_kept()  # staged output deleted once landed
        if counted:  # counted on its batch by the reconcile's notify
            assert row.get("counted") is True


BUCKETS = pytest.mark.parametrize("bucket", [VERSIONED, UNVERSIONED])


def _aborted_first(abort):
    """CompleteMultipartUpload side_effect: the abort lands first, then S3 refuses the completion
    with NoSuchUpload (moto raises a KeyError for a completed-after-abort upload instead)."""
    def call(**_kwargs):
        abort()
        raise ClientError({"Error": {"Code": "NoSuchUpload", "Message": "aborted"}},
                          "CompleteMultipartUpload")
    return call


def _then(real, before):
    """side_effect that runs ``before`` and then the real call."""
    def call(*args, **kwargs):
        before()
        return real(*args, **kwargs)
    return call


# --------------------------------------------------------------------------------------------- #
# The reconcile of a console stop
# --------------------------------------------------------------------------------------------- #

@BUCKETS
def test_reconcile_abort_before_complete(env, machine, bucket):
    run = _Run(env, bucket)
    run.console_stop()
    engine.reconcile(EXEC_ARN)
    with pytest.raises(loc.CopyRefused):
        engine.complete(run.pj)
    run.assert_not_landed(loc.STATUS_FAILED)


@BUCKETS
def test_reconcile_abort_during_complete_before_its_list_parts(env, machine, bucket):
    """The reviewer's repro (M10): before, SUCCEEDED on the old object, staged output deleted."""
    run = _Run(env, bucket)
    run.console_stop()
    with patch.object(engine, "_listed_parts", side_effect=_then(engine._listed_parts, run.abort)):
        with pytest.raises(loc.CopyRefused):
            engine.complete(run.pj)
    engine.reconcile(EXEC_ARN)
    run.assert_not_landed(loc.STATUS_FAILED)


@BUCKETS
def test_reconcile_abort_during_complete_before_its_complete_call(env, machine, bucket):
    run = _Run(env, bucket)
    run.console_stop()
    with patch.object(env["s3"], "complete_multipart_upload", side_effect=_aborted_first(run.abort)):
        with pytest.raises(loc.CopyRefused):
            engine.complete(run.pj)
    engine.reconcile(EXEC_ARN)
    run.assert_not_landed(loc.STATUS_FAILED)


@BUCKETS
def test_reconcile_finalizing_during_complete(env, machine, bucket):
    """The whole reconcile (abort and its FAILED finalize) lands while Complete checks its parts."""
    run = _Run(env, bucket)
    run.console_stop()
    with patch.object(engine, "_listed_parts",
                      side_effect=_then(engine._listed_parts, lambda: engine.reconcile(EXEC_ARN))):
        with pytest.raises(loc.CopyRefused):
            engine.complete(run.pj)
    run.assert_not_landed(loc.STATUS_FAILED)
    assert run.audits() == [True]  # one rejected entry


@BUCKETS
def test_reconcile_after_complete_landed_keeps_the_new_object(env, machine, bucket):
    """Positive control: CompleteMultipartUpload returned before the stop; the new object wins."""
    run = _Run(env, bucket)
    run.complete_by_hand()
    run.console_stop()
    engine.reconcile(EXEC_ARN)
    engine.complete(run.pj)  # the in-flight Complete resumes: NoSuchUpload, its own token
    run.assert_landed()
    assert run.row()["reconciledStatus"] == "ABORTED"


@BUCKETS
def test_complete_that_lost_its_completion_response_finds_its_own_object(env, machine, bucket):
    """Complete's own lost-result retry (no stop at all): the object carries its token."""
    run = _Run(env, bucket)
    run.complete_by_hand()
    engine.complete(run.pj)
    run.assert_landed(counted=False)  # the Notify state counts it next


@BUCKETS
def test_an_upload_removed_outside_vams_over_an_old_object_fails_the_job(env, machine, bucket):
    """A lifecycle rule removed the upload; no stop. Complete refuses and the Fail state finalizes."""
    run = _Run(env, bucket)
    run.abort()
    with pytest.raises(loc.CopyRefused):
        engine.complete(run.pj)
    assert run.row()["status"] == loc.STATUS_RUNNING  # left for the Fail state
    engine.fail(run.pj, {"Error": "CopyRefused", "Cause": "{}"})
    run.assert_not_landed(loc.STATUS_FAILED)


# --------------------------------------------------------------------------------------------- #
# The cancel path (abort route, error handler), and the engine's reconcile of the stop it made
# --------------------------------------------------------------------------------------------- #

@BUCKETS
def test_cancel_before_complete(env, machine, bucket):
    run = _Run(env, bucket)
    assert run.cancel() == []
    with pytest.raises(loc.CopyRefused):
        engine.complete(run.pj)
    engine.reconcile(EXEC_ARN)  # the stop's status change event
    run.assert_not_landed(loc.STATUS_CANCELLED)


@BUCKETS
def test_cancel_during_complete_before_its_list_parts(env, machine, bucket):
    run = _Run(env, bucket)
    with patch.object(engine, "_listed_parts", side_effect=_then(engine._listed_parts, run.cancel)):
        with pytest.raises(loc.CopyRefused):
            engine.complete(run.pj)
    engine.reconcile(EXEC_ARN)
    run.assert_not_landed(loc.STATUS_CANCELLED)


@BUCKETS
def test_cancel_abort_during_complete_with_its_decision_after_complete(env, machine, bucket):
    """The cancel's abort lands before Complete's CompleteMultipartUpload; Complete then decides
    before the cancel path writes CANCELLED."""
    run = _Run(env, bucket)
    run.env["sfn"].describe_execution.return_value = _described(
        run.job, status="ABORTED", error=loc.STOP_ERROR_CANCELLED)
    with patch.object(env["s3"], "complete_multipart_upload", side_effect=_aborted_first(run.abort)):
        with pytest.raises(loc.CopyRefused):
            engine.complete(run.pj)
    assert run.row()["status"] == loc.STATUS_RUNNING  # Complete wrote nothing
    run.cancel()
    engine.reconcile(EXEC_ARN)
    run.assert_not_landed(loc.STATUS_CANCELLED)


@BUCKETS
def test_cancel_after_complete_landed_finishes_once_even_if_complete_died(env, machine, bucket):
    """L13: Complete's invocation ended after CompleteMultipartUpload and before its finalize, and
    the cancel stopped its execution, so no Step Functions retry runs. The cancel path keeps the
    landing; the engine's reconcile of the stop does the post-landing work once."""
    run = _Run(env, bucket)
    run.complete_by_hand()
    assert run.cancel() == []
    assert run.row()["status"] == loc.STATUS_SUCCEEDED
    env["audit"].assert_not_called()
    engine.reconcile(EXEC_ARN)
    run.assert_landed()
    engine.reconcile(EXEC_ARN)  # a redelivered event changes nothing
    run.assert_landed()


@BUCKETS
def test_cancel_after_complete_landed_with_complete_still_running(env, machine, bucket):
    run = _Run(env, bucket)
    run.complete_by_hand()
    run.cancel()
    engine.complete(run.pj)  # the in-flight Complete resumes
    engine.reconcile(EXEC_ARN)
    run.assert_landed()


@BUCKETS
def test_stop_event_before_the_cancel_path_reaches_the_job(env, machine, bucket):
    """The engine's reconcile of the cancel's stop runs first: it makes the cancel path's decision
    (here CANCELLED, the upload was still open), and the cancel path then changes nothing."""
    run = _Run(env, bucket)
    run.env["sfn"].describe_execution.return_value = _described(
        run.job, status="ABORTED", error=loc.STOP_ERROR_CANCELLED)
    engine.reconcile(EXEC_ARN)
    assert run.row()["status"] == loc.STATUS_CANCELLED
    run.cancel()
    with pytest.raises(loc.CopyRefused):
        engine.complete(run.pj)
    run.assert_not_landed(loc.STATUS_CANCELLED)


@BUCKETS
def test_stop_event_before_the_cancel_path_after_complete_landed(env, machine, bucket):
    run = _Run(env, bucket)
    run.complete_by_hand()
    run.env["sfn"].describe_execution.return_value = _described(
        run.job, status="ABORTED", error=loc.STOP_ERROR_CANCELLED)
    engine.reconcile(EXEC_ARN)
    run.cancel()
    run.assert_landed()


class _DeniedHead:
    """The abort route's and the error handler's S3 client: AbortMultipartUpload only (no
    s3:GetObject on the asset buckets, so HeadObject is denied)."""

    def __init__(self, s3):
        self._s3 = s3

    def __getattr__(self, name):
        if name == "head_object":
            def denied(**_kwargs):
                raise ClientError({"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject")
            return denied
        return getattr(self._s3, name)


@BUCKETS
def test_a_cancel_that_cannot_read_the_object_leaves_a_landed_job_to_the_engine(env, machine, bucket):
    """D9: before, a cancel path without HeadObject cancelled a job whose file had landed."""
    run = _Run(env, bucket)
    run.complete_by_hand()
    assert run.cancel(_DeniedHead(env["s3"])) == []
    assert run.row()["status"] == loc.STATUS_RUNNING  # undecided, left to the engine
    engine.reconcile(EXEC_ARN)
    run.assert_landed()


@BUCKETS
def test_a_cancel_that_cannot_read_the_object_over_an_old_object(env, machine, bucket):
    """D9 with the M10 ordering: the cancel path's abort and an abort before it; the engine decides
    from the token, not from the old object of the same size."""
    run = _Run(env, bucket)
    run.abort()  # an earlier abort (the upload is gone, nothing of this job landed)
    run.cancel(_DeniedHead(env["s3"]))
    assert run.row()["status"] == loc.STATUS_RUNNING
    engine.reconcile(EXEC_ARN)
    run.assert_not_landed(loc.STATUS_CANCELLED)


# --------------------------------------------------------------------------------------------- #
# Round 5 (M11): the job the cancel path leaves open is never left without a decider
# --------------------------------------------------------------------------------------------- #

def _backstop(env):
    """What the execution details read hands the copy engine (executionService
    _reconcile_ended_copies over the run's job rows) when the stop's status change event is lost."""
    return loc.ended_copy_executions(env["sfn"], loc.jobs_for_execution(env["jobs"], "e2"))


@BUCKETS
def test_a_lost_stop_event_is_reconciled_by_the_details_read_for_a_landed_job(env, machine, bucket):
    """Before, the backstop skipped a cancelled stop, so the open job stayed RUNNING until its TTL."""
    run = _Run(env, bucket)
    run.complete_by_hand()
    assert run.cancel(_DeniedHead(env["s3"])) == []
    assert run.row()["status"] == loc.STATUS_RUNNING  # left to the engine; its event is lost
    assert _backstop(env) == [EXEC_ARN]
    engine.reconcile(EXEC_ARN)
    run.assert_landed()
    assert _backstop(env) == []  # decided: the next read hands back nothing
    engine.reconcile(EXEC_ARN)  # the late event (or a second read) changes nothing
    run.assert_landed()


@BUCKETS
def test_a_lost_stop_event_is_reconciled_by_the_details_read_over_an_old_object(env, machine, bucket):
    run = _Run(env, bucket)
    run.abort()
    run.cancel(_DeniedHead(env["s3"]))
    assert run.row()["status"] == loc.STATUS_RUNNING
    assert _backstop(env) == [EXEC_ARN]
    engine.reconcile(EXEC_ARN)
    run.assert_not_landed(loc.STATUS_CANCELLED)
    assert _backstop(env) == []
    engine.reconcile(EXEC_ARN)
    run.assert_not_landed(loc.STATUS_CANCELLED)


def _no_execution(env):
    env["sfn"].stop_execution.side_effect = ClientError(
        {"Error": {"Code": "ExecutionDoesNotExist", "Message": "gone"}}, "StopExecution")


@BUCKETS
def test_a_cancel_that_made_no_stop_does_not_leave_the_job_open(env, machine, bucket):
    """StopExecution answered ExecutionDoesNotExist: no stop was made, so no status change event
    comes and the details read has no cancelled stop to find. The cancel path decides the job."""
    run = _Run(env, bucket)
    run.complete_by_hand()
    _no_execution(env)
    assert run.cancel(_DeniedHead(env["s3"])) == []
    assert run.row()["status"] == loc.STATUS_CANCELLED
    assert not loc.is_open_job(run.row())


@BUCKETS
def test_a_cancel_that_made_no_stop_still_keeps_a_landing_it_can_read(env, machine, bucket):
    """Positive control: with read access the cancel path decides by the job's token as before."""
    run = _Run(env, bucket)
    run.complete_by_hand()
    _no_execution(env)
    assert run.cancel() == []
    assert run.row()["status"] == loc.STATUS_SUCCEEDED


# --------------------------------------------------------------------------------------------- #
# The token itself
# --------------------------------------------------------------------------------------------- #

@BUCKETS
def test_each_upload_carries_its_own_token(env, machine, bucket):
    run = _Run(env, bucket)
    row = run.row()
    token = row["destUploadToken"]
    assert token and token != run.old["Metadata"].get(TOKEN_KEY)
    assert run.old["Metadata"].get(TOKEN_KEY)  # the earlier run's object carries ITS token
    run.complete_by_hand()
    assert run.head()["Metadata"][TOKEN_KEY] == token


def test_a_row_without_a_token_has_no_landed_object(env, machine):
    """No size or presence fallback: rows without destUploadToken never count an object."""
    run = _Run(env, VERSIONED)
    run.complete_by_hand()
    head = MagicMock(side_effect=AssertionError("no read is needed"))
    with patch.object(env["s3"], "head_object", head):
        assert loc.landed_object_version(env["s3"], {
            "destBucket": VERSIONED, "destKey": "a1/big.bin", "sourceSize": len(run.new_body),
            "partCount": 3}) == (False, "")


def test_the_token_key_is_system_metadata():
    from backend.backend.common import s3MetadataKeys as keys
    assert keys.VAMS_COPY_UPLOAD_TOKEN_METADATA_KEY == TOKEN_KEY
    assert keys.is_system_metadata_key(TOKEN_KEY)  # excluded from search indexing
