# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Round 3 of #1123 (M8): the reconcile, or the cancel path, against a task of the stopped copy
execution that is still running.

StopExecution ends a Step Functions execution but not a Lambda invocation already in flight, so a
Prepare or Complete of a stopped copy can keep running after the reconcile finalized its job. Before
this change Prepare moved a FAILED job back to RUNNING with an open multipart upload, and Complete
moved it FAILED -> SUCCEEDED with two audit entries and two upload outcomes.

The job status is monotonic now (every writer is conditional on the job being open), and one truth
holds whatever the order: a landed object wins. The reconcile aborts the multipart upload before it
decides, so no task can land the file afterwards; an upload already gone was completed by Complete,
and the job is finalized SUCCEEDED when the destination holds the planned object. Each ordering
ends with one status, one audit entry and one upload outcome."""

from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.assets.test_largeObjectCopy_engine import (  # noqa: F401 (fixture)
    _uploads_table, engine, env, loc)
from backend.tests.handlers.assets.test_largeObjectCopy_reconcile import (  # noqa: F401 (fixture)
    EXEC_ARN, _described, _job_with_execution, _waiting_batch, machine)

DEST = {"Bucket": "asset-bucket", "Key": "a1/big.bin"}


def _open_uploads(env):
    return env["s3"].list_multipart_uploads(Bucket="asset-bucket").get("Uploads", [])


def _landed(env):
    return "Contents" in env["s3"].list_objects_v2(Bucket="asset-bucket", Prefix="a1/big.bin")


def _entry(uploads):
    return uploads.get_item(Key={"uploadId": "y1", "assetId": "a1"})["Item"]["asyncFiles"]["big.bin"]


def _audits(env):
    """denied flag of each audit entry written."""
    return [c.args[4] for c in env["audit"].call_args_list]


def _copied(env, kind=loc.KIND_WORKFLOW_OUTPUT):
    """A job prepared and fully copied, its Complete not run yet."""
    job = _job_with_execution(env, kind=kind)
    if kind == loc.KIND_WORKFLOW_OUTPUT:
        _waiting_batch(env, job)
    prepared = engine.prepare(job)
    for batch in prepared["batches"]:
        engine.copy_batch(prepared["job"], batch)
    return job, prepared["job"]


def _complete_upload_by_hand(env, pj):
    """What an in-flight Complete has done once CompleteMultipartUpload returned."""
    parts = env["s3"].list_parts(UploadId=pj["uploadIdS3"], **DEST)["Parts"]
    env["s3"].complete_multipart_upload(
        UploadId=pj["uploadIdS3"], **DEST,
        MultipartUpload={"Parts": [{"PartNumber": p["PartNumber"], "ETag": p["ETag"]} for p in parts]})


def _console_stop(env, job):
    env["sfn"].describe_execution.return_value = _described(job, status="ABORTED", error="")


# --------------------------------------------------------------------------------------------- #
# Prepare in flight
# --------------------------------------------------------------------------------------------- #

def test_a_prepare_that_starts_after_the_reconcile_does_nothing(env, machine):
    job = _job_with_execution(env)
    _waiting_batch(env, job)
    _console_stop(env, job)
    engine.reconcile(EXEC_ARN)
    with patch.object(env["s3"], "create_multipart_upload") as create, \
            patch.object(env["s3"], "head_object") as head:
        with pytest.raises(loc.CopyRefused):
            engine.prepare(job)
    # Checked before acting: no source read, no upload created and aborted
    create.assert_not_called()
    head.assert_not_called()
    row = loc.get_job_row(env["jobs"], job["jobId"])
    assert row["status"] == loc.STATUS_FAILED and "uploadIdS3" not in row
    assert _open_uploads(env) == []


def test_a_prepare_in_flight_when_the_reconcile_lands_aborts_its_upload(env, machine):
    """Before: the row went FAILED -> RUNNING with counted set and an open multipart upload."""
    job = _job_with_execution(env)
    _waiting_batch(env, job)
    _console_stop(env, job)
    real_create = env["s3"].create_multipart_upload

    def create_then_reconcile(**kwargs):
        created = real_create(**kwargs)
        engine.reconcile(EXEC_ARN)  # the stop's status change event arrives now
        return created

    with patch.object(env["s3"], "create_multipart_upload", side_effect=create_then_reconcile):
        with pytest.raises(loc.CopyRefused):
            engine.prepare(job)
    row = loc.get_job_row(env["jobs"], job["jobId"])
    assert row["status"] == loc.STATUS_FAILED
    assert row["counted"] is True and "uploadIdS3" not in row
    assert _open_uploads(env) == []
    assert _audits(env) == [True]
    env["sfn"].send_task_success.assert_called_once()


# --------------------------------------------------------------------------------------------- #
# Complete in flight: both orderings
# --------------------------------------------------------------------------------------------- #

def test_complete_landed_before_the_reconcile_the_landed_object_wins(env, machine):
    """Ordering 1: CompleteMultipartUpload had returned when the stop landed. Before: FAILED, then
    SUCCEEDED, with a rejected and a completed audit entry and two upload outcomes."""
    uploads = _uploads_table()
    job, pj = _copied(env, kind=loc.KIND_UPLOAD)
    _complete_upload_by_hand(env, pj)
    _console_stop(env, job)

    engine.reconcile(EXEC_ARN)
    after_reconcile = loc.get_job_row(env["jobs"], job["jobId"])
    engine.complete(pj)  # the in-flight Complete resumes

    row = loc.get_job_row(env["jobs"], job["jobId"])
    assert after_reconcile["status"] == loc.STATUS_SUCCEEDED
    assert after_reconcile["reconciledStatus"] == "ABORTED"
    assert row["status"] == loc.STATUS_SUCCEEDED and row["counted"] is True
    assert row["destVersionId"] == env["s3"].head_object(**DEST)["VersionId"]
    assert _landed(env)
    assert _audits(env) == [False]  # one completion entry
    assert _entry(uploads)["status"] == "SUCCEEDED"
    env["lambda"].invoke.assert_called_once()  # one subscription email
    # The staged object is deleted once the file landed
    assert env["s3"].list_object_versions(Bucket="run-bucket", Prefix="pipelines/").get("Versions", []) == []


def test_complete_finishing_after_the_reconcile_cannot_land_the_file(env, machine):
    """Ordering 2: the stop landed while Complete was still checking its parts. The reconcile aborts
    the upload first, so Complete's CompleteMultipartUpload is refused and nothing lands."""
    uploads = _uploads_table()
    job, pj = _copied(env, kind=loc.KIND_UPLOAD)
    _console_stop(env, job)
    real_listed = engine._listed_parts

    def listed_then_reconcile(*args, **kwargs):
        parts = real_listed(*args, **kwargs)
        engine.reconcile(EXEC_ARN)
        return parts

    with patch.object(engine, "_listed_parts", side_effect=listed_then_reconcile):
        with pytest.raises(Exception):
            engine.complete(pj)
    row = loc.get_job_row(env["jobs"], job["jobId"])
    assert row["status"] == loc.STATUS_FAILED and row["counted"] is True
    assert not _landed(env)
    assert _open_uploads(env) == []
    assert _audits(env) == [True]
    assert _entry(uploads)["status"] == "FAILED"


def test_a_complete_that_loses_the_finalize_supersedes_it_with_its_landed_object(env, machine):
    """A remover finalized the job FAILED while this Complete's CompleteMultipartUpload was in
    flight. Before #1123 D1 this was taken to need an upload removed outside VAMS and the stored
    FAILED stood (the object was the asset's current version with no version id, audit or staged
    delete). S3 answers 204 to an abort sent during a Complete that then succeeds, so it happens
    inside VAMS: the landed object wins, the job is superseded SUCCEEDED once and finished."""
    job, pj = _copied(env)
    real_complete = env["s3"].complete_multipart_upload

    def complete_then_finalized_elsewhere(**kwargs):
        done = real_complete(**kwargs)
        loc.finalize_job(env["jobs"], job["jobId"], loc.STATUS_FAILED, {"error": loc.REASON_COPY_FAILED})
        return done

    with patch.object(env["s3"], "complete_multipart_upload", side_effect=complete_then_finalized_elsewhere):
        result = engine.complete(pj)
    row = loc.get_job_row(env["jobs"], job["jobId"])
    assert row["status"] == loc.STATUS_SUCCEEDED
    assert row["supersededStatus"] == loc.STATUS_FAILED and "error" not in row
    assert result["job"]["destVersionId"] == row["destVersionId"]
    assert _landed(env)
    assert _audits(env) == [False]
    env["lambda"].invoke.assert_called_once()


def test_a_complete_that_starts_after_the_reconcile_aborts_and_stops(env, machine):
    job, pj = _copied(env)
    _console_stop(env, job)
    engine.reconcile(EXEC_ARN)  # aborted the upload, FAILED
    with pytest.raises(loc.CopyRefused):
        engine.complete(pj)
    assert loc.get_job_row(env["jobs"], job["jobId"])["status"] == loc.STATUS_FAILED
    assert not _landed(env)
    assert _audits(env) == [True]


def test_the_fail_state_after_a_finalized_job_writes_nothing_more(env, machine):
    """A Complete refused because the job was finalized goes to Fail: Fail keeps the stored outcome
    and writes no second audit entry or outcome."""
    uploads = _uploads_table()
    job, pj = _copied(env, kind=loc.KIND_UPLOAD)
    _complete_upload_by_hand(env, pj)
    _console_stop(env, job)
    engine.reconcile(EXEC_ARN)  # landed: SUCCEEDED
    result = engine.fail(pj, {"Error": "States.TaskFailed", "Cause": "{}"})
    assert loc.get_job_row(env["jobs"], job["jobId"])["status"] == loc.STATUS_SUCCEEDED
    assert result["refused"] is True  # not counted as a broken copy
    assert _audits(env) == [False]
    assert _entry(uploads)["status"] == "SUCCEEDED"


def test_a_reconcile_cannot_finalize_without_aborting_the_upload(env, machine):
    """An abort that fails for another reason than a missing upload is raised, so the reconcile is
    retried rather than deciding while a task could still land the file."""
    job, _pj = _copied(env)
    _console_stop(env, job)
    from botocore.exceptions import ClientError
    denied = ClientError({"Error": {"Code": "SlowDown"}}, "AbortMultipartUpload")
    with patch.object(env["s3"], "abort_multipart_upload", side_effect=denied):
        with pytest.raises(ClientError):
            engine.reconcile(EXEC_ARN)
    assert loc.get_job_row(env["jobs"], job["jobId"])["status"] == loc.STATUS_RUNNING
    env["audit"].assert_not_called()


def test_an_upload_gone_without_the_planned_object_is_not_taken_as_landed(env, machine):
    """An upload removed outside VAMS (a bucket lifecycle rule) with no landed object: FAILED."""
    job, pj = _copied(env)
    env["s3"].abort_multipart_upload(UploadId=pj["uploadIdS3"], **DEST)
    _console_stop(env, job)
    engine.reconcile(EXEC_ARN)
    assert loc.get_job_row(env["jobs"], job["jobId"])["status"] == loc.STATUS_FAILED


def test_an_earlier_object_at_the_destination_is_not_taken_as_landed(env, machine):
    """The destination already held an object of the same size and part count before this job's
    upload was created (a re-run of the same output): it is not this job's landing."""
    first, first_pj = _copied(env)
    engine.complete(first_pj)
    # A second job for the same destination (another run), its upload removed outside VAMS
    second = loc.build_job(**{**_job_kwargs(), "scope_id": "e2", "batch_id": "e2",
                              "workflow_execution_id": "e2"})
    starter = MagicMock(**{"start_execution.return_value": {"executionArn": "b"}})
    loc.start_copy_job(env["jobs"], starter, "arn:aws:states:r:1:stateMachine:sm", second)
    loc.update_job_row(env["jobs"], second["jobId"], {"executionArn": EXEC_ARN})
    env["s3"].put_object(Bucket="run-bucket", Key=second["source"]["key"],
                         Body=env["s3"].get_object(**DEST)["Body"].read())
    pj = engine.prepare(second)["job"]
    env["s3"].abort_multipart_upload(UploadId=pj["uploadIdS3"], **DEST)
    _console_stop(env, second)
    engine.reconcile(EXEC_ARN)
    assert loc.get_job_row(env["jobs"], second["jobId"])["status"] == loc.STATUS_FAILED


def _job_kwargs():
    return dict(
        kind=loc.KIND_WORKFLOW_OUTPUT,
        source={"bucket": "run-bucket", "key": "pipelines/p/j/output/e2/files/big.bin"},
        dest={"bucket": "asset-bucket", "key": "a1/big.bin"},
        metadata={"databaseid": "db", "assetid": "a1"}, content_headers={}, database_id="db",
        asset_id="a1", upload_id="y1", upload_type="assetFile", relative_key="big.bin",
        change_user_id="u1", scope_id="e2", workflow_execution_id="e2", workflow_id="wf",
        workflow_database_id="GLOBAL", batch_id="e2")


# --------------------------------------------------------------------------------------------- #
# The cancel path against an in-flight Complete
# --------------------------------------------------------------------------------------------- #

def test_a_cancel_after_complete_landed_keeps_the_file_as_succeeded(env, machine):
    """Before: CANCELLED, then overwritten SUCCEEDED by the in-flight Complete."""
    job, pj = _copied(env)
    _complete_upload_by_hand(env, pj)
    warnings = loc.cancel_execution_copy_jobs(env["jobs"], env["sfn"], env["s3"], "e1")
    after_cancel = loc.get_job_row(env["jobs"], job["jobId"])
    engine.complete(pj)  # the in-flight Complete resumes and does the post-landing work once
    row = loc.get_job_row(env["jobs"], job["jobId"])
    assert warnings == []
    assert after_cancel["status"] == loc.STATUS_SUCCEEDED
    assert row["status"] == loc.STATUS_SUCCEEDED
    assert _audits(env) == [False]
    env["lambda"].invoke.assert_called_once()


def test_a_cancel_before_complete_landed_is_final(env, machine):
    job, pj = _copied(env)
    loc.cancel_execution_copy_jobs(env["jobs"], env["sfn"], env["s3"], "e1")
    with pytest.raises(loc.CopyRefused):
        engine.complete(pj)
    row = loc.get_job_row(env["jobs"], job["jobId"])
    assert row["status"] == loc.STATUS_CANCELLED
    assert not _landed(env)
    env["audit"].assert_not_called()


# --------------------------------------------------------------------------------------------- #
# Every writer is conditional on the job being open
# --------------------------------------------------------------------------------------------- #

@pytest.mark.parametrize("terminal", [loc.STATUS_SUCCEEDED, loc.STATUS_FAILED, loc.STATUS_CANCELLED])
def test_no_writer_moves_a_terminal_job(env, terminal):
    job = _job_with_execution(env)
    loc.update_job_row(env["jobs"], job["jobId"], {"status": terminal})
    for status in (loc.STATUS_SUCCEEDED, loc.STATUS_FAILED):
        assert loc.finalize_job(env["jobs"], job["jobId"], status) is None
    assert not loc.update_job_row(env["jobs"], job["jobId"], {"status": loc.STATUS_RUNNING},
                                  condition=loc.OPEN_JOB_CONDITION,
                                  condition_values=dict(loc.OPEN_JOB_VALUES))
    engine.notify(job, loc.STATUS_FAILED if terminal == loc.STATUS_SUCCEEDED else loc.STATUS_SUCCEEDED)
    assert loc.get_job_row(env["jobs"], job["jobId"])["status"] == terminal


# --------------------------------------------------------------------------------------------- #
# M8 part 2: the upload outcome is retried until written
# --------------------------------------------------------------------------------------------- #

def test_a_failed_outcome_write_fails_complete_and_its_retry_records_it(env):
    """Before: a failed outcome write was logged and dropped; the file stayed PENDING for 7 days."""
    uploads = _uploads_table()
    job = _job_with_execution(env, kind=loc.KIND_UPLOAD)
    prepared = engine.prepare(job)
    for batch in prepared["batches"]:
        engine.copy_batch(prepared["job"], batch)
    from botocore.exceptions import ClientError
    throttled = ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "UpdateItem")
    with patch.object(engine.upload_table, "update_item", side_effect=throttled):
        with pytest.raises(ClientError):
            engine.complete(prepared["job"])
    assert _entry(uploads)["status"] == "PENDING"
    assert loc.get_job_row(env["jobs"], job["jobId"])["status"] == loc.STATUS_SUCCEEDED
    engine.complete(prepared["job"])  # the Step Functions retry of Complete
    assert _entry(uploads)["status"] == "SUCCEEDED"
    assert _audits(env) == [False]  # the retry writes no second entry
    env["lambda"].invoke.assert_called_once()
