# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""#1123 in-dev D5, live recheck on demo21: an abort's immediate answer is not final.

All three cancel paths (the Executions UI abort, a StopExecution of the copy execution, a
StopExecution of the workflow execution) still left 1-2 parts after their cleanup. Every sweep ended
on ListParts = NoSuchUpload right after its own abort, yet the upload was listed again 2 to 8 minutes
later with a part whose copy S3 had accepted before the stop; one later manual abort removed it for
good.

Now every stopped or failed job with a recorded upload gets a delayed sweep: an execution of the
copy state machine's sweep branch (Wait 5 min, sweep, Wait 10 min, sweep), started once per job by
the cancel path and by the engine's failure cleanup. Each pass decides by the landed object first,
lists the uploads at the job's exact key and aborts only the upload the job row recorded.

``_GhostS3`` is moto with what demo21 showed: the first abort of an upload answers 204 and does not
remove it; for ``hidden_for`` seconds afterwards ListParts and a repeated abort answer NoSuchUpload
and ListMultipartUploads does not list it; after that it is listed again with its parts, and an
abort removes it.

The end-to-end tests fail on 6eeb32269 (no sweep is scheduled, the upload stays listed)."""

import json
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from backend.tests.handlers.assets.test_largeObjectCopy_engine import (  # noqa: F401 (fixture)
    SMALL_PARTS, engine, env, loc)
from backend.tests.handlers.assets.test_largeObjectCopy_reconcile import (  # noqa: F401 (fixture)
    EXEC_ARN, MACHINE_ARN, _described, machine)

VERSIONED = "asset-bucket"
UNVERSIONED = "ext-bucket"  # an external bucket: no versioning, no VAMS lifecycle rule
BUCKETS = pytest.mark.parametrize("bucket", [VERSIONED, UNVERSIONED])
SOURCE_KEY = "pipelines/p/j/output/e1/files/big.bin"
DEST_KEY = "a1/big.bin"
FIRST = loc.SWEEP_FIRST_WAIT_SECONDS
FINAL = loc.SWEEP_FIRST_WAIT_SECONDS + loc.SWEEP_SECOND_WAIT_SECONDS


def _no_such_upload(op):
    return ClientError({"Error": {"Code": "NoSuchUpload", "Message": "gone"}}, op)


class _GhostS3:
    """moto's S3 with the abort behaviour demo21 showed (see the module docstring)."""

    def __init__(self, s3, hidden_for):
        self._s3 = s3
        self.hidden_for = hidden_for
        self.now = 0
        self.hidden_until = {}
        self.aborts = []

    def _hidden(self, upload_id):
        return upload_id in self.hidden_until and self.now < self.hidden_until[upload_id]

    def abort_multipart_upload(self, **kwargs):
        upload_id = kwargs["UploadId"]
        self.aborts.append((self.now, upload_id))
        if upload_id not in self.hidden_until:
            # The first abort: answered, and the upload stays (a part copy was accepted earlier).
            self.hidden_until[upload_id] = self.now + self.hidden_for
            return {"ResponseMetadata": {"HTTPStatusCode": 204}}
        if self._hidden(upload_id):
            raise _no_such_upload("AbortMultipartUpload")
        return self._s3.abort_multipart_upload(**kwargs)

    def list_parts(self, **kwargs):
        if self._hidden(kwargs["UploadId"]):
            raise _no_such_upload("ListParts")
        return self._s3.list_parts(**kwargs)

    def list_multipart_uploads(self, **kwargs):
        response = dict(self._s3.list_multipart_uploads(**kwargs))
        response["Uploads"] = [u for u in response.get("Uploads", []) or []
                               if not self._hidden(u["UploadId"])]
        return response

    def __getattr__(self, name):
        return getattr(self._s3, name)


def _start(env, bucket):
    if bucket == UNVERSIONED:
        env["s3"].create_bucket(Bucket=UNVERSIONED)
    job = loc.build_job(
        kind=loc.KIND_WORKFLOW_OUTPUT,
        source={"bucket": "run-bucket", "key": SOURCE_KEY},
        dest={"bucket": bucket, "key": DEST_KEY},
        metadata={"databaseid": "db", "assetid": "a1"}, content_headers={}, database_id="db",
        asset_id="a1", upload_id="y1", upload_type="assetFile", relative_key="big.bin",
        change_user_id="u1", scope_id="e1", workflow_execution_id="e1", workflow_id="wf",
        workflow_database_id="GLOBAL", batch_id="e1")
    starter = MagicMock(**{"start_execution.return_value": {"executionArn": EXEC_ARN}})
    loc.start_copy_job(env["jobs"], starter, MACHINE_ARN, job)
    prepared = engine.prepare(job)
    # One batch copied before the stop: its parts are what stays behind.
    engine.copy_batch(prepared["job"], prepared["batches"][0])
    return job, prepared


def _uploads(env, bucket, key=DEST_KEY):
    return [u for u in env["s3"].list_multipart_uploads(Bucket=bucket).get("Uploads", [])
            if u["Key"] == key]


def _row(env, job):
    return loc.get_job_row(env["jobs"], job["jobId"])


def _sweep_starts(env):
    return [c.kwargs for c in env["sfn"].start_execution.call_args_list
            if c.kwargs.get("name", "").startswith(loc.SWEEP_EXECUTION_PREFIX)]


def _run_sweep_execution(env, ghost, start_kwargs):
    """What the copy state machine's sweep branch does with the execution's input."""
    assert start_kwargs["stateMachineArn"] == MACHINE_ARN
    request = json.loads(start_kwargs["input"])["sweep"]
    ghost.now = FIRST
    engine.lambda_handler({"action": "sweep", "sweep": request, "final": False}, None)
    ghost.now = FINAL
    return engine.lambda_handler({"action": "sweep", "sweep": request, "final": True}, None)


def _reconcile(env, job, status="ABORTED", error=loc.STOP_ERROR_CANCELLED):
    env["sfn"].describe_execution.return_value = _described(job, status=status, error=error)
    return engine.reconcile(EXEC_ARN)


# --------------------------------------------------------------------------------------------- #
# End to end: each cancel path leaves zero parts once the delayed sweep ran
# --------------------------------------------------------------------------------------------- #

@BUCKETS
@pytest.mark.parametrize("hidden_for", [120, 480])  # listed again 2 and 8 minutes later (demo21)
def test_the_cancel_path_leaves_no_part_after_the_delayed_sweep(env, machine, bucket, hidden_for):
    """The Executions UI abort and a StopExecution of the workflow execution (both reach the cancel
    path), then the engine's reconcile of that stop."""
    job, _prepared = _start(env, bucket)
    ghost = _GhostS3(env["s3"], hidden_for)
    with patch.object(engine, "_client_for_bucket", lambda b: ghost):
        loc.cancel_execution_copy_jobs(env["jobs"], env["sfn"], ghost, "e1")
        _reconcile(env, job)
        # Every immediate cleanup ended on NoSuchUpload, and the upload is still there (D5).
        assert _row(env, job)["status"] == loc.STATUS_CANCELLED
        assert len(_uploads(env, bucket)) == 1
        starts = _sweep_starts(env)
        assert starts, "no delayed sweep was scheduled"
        # One execution per job: every scheduler uses the same name and input.
        assert {(s["name"], s["input"]) for s in starts} == {
            (loc.sweep_execution_name_for(job["jobId"]), loc.sweep_input(job["jobId"]))}
        result = _run_sweep_execution(env, ghost, starts[0])
    assert _uploads(env, bucket) == []
    assert result["remaining"] is False
    # The landed decision was not touched.
    assert _row(env, job)["status"] == loc.STATUS_CANCELLED


@BUCKETS
def test_a_stop_of_the_copy_execution_leaves_no_part_after_the_delayed_sweep(env, machine, bucket):
    """A StopExecution of the copy execution (console or API): the reconcile fails the job, its
    cleanup aborts until NoSuchUpload, and the delayed sweep removes what lands later."""
    job, _prepared = _start(env, bucket)
    ghost = _GhostS3(env["s3"], 300)
    with patch.object(engine, "_client_for_bucket", lambda b: ghost):
        _reconcile(env, job, status="ABORTED", error="")
        assert _row(env, job)["status"] == loc.STATUS_FAILED
        assert len(_uploads(env, bucket)) == 1
        starts = _sweep_starts(env)
        assert len(starts) == 1
        _run_sweep_execution(env, ghost, starts[0])
    assert _uploads(env, bucket) == []


@BUCKETS
def test_a_failed_copy_leaves_no_part_after_the_delayed_sweep(env, machine, bucket):
    """The state machine's failure path (fail action) schedules the sweep as well."""
    job, prepared = _start(env, bucket)
    ghost = _GhostS3(env["s3"], 200)
    with patch.object(engine, "_client_for_bucket", lambda b: ghost):
        engine.fail(prepared["job"], {"Error": "States.TaskFailed", "Cause": ""})
        assert len(_uploads(env, bucket)) == 1
        starts = _sweep_starts(env)
        assert len(starts) == 1
        _run_sweep_execution(env, ghost, starts[0])
    assert _uploads(env, bucket) == []


# --------------------------------------------------------------------------------------------- #
# One pass: what the sweep may and may not touch
# --------------------------------------------------------------------------------------------- #

def _cancelled_with_upload(env, bucket=VERSIONED):
    job, prepared = _start(env, bucket)
    loc.mark_cancelled(env["jobs"], job["jobId"])
    return job, prepared


def test_the_sweep_never_aborts_another_writers_upload(env, machine):
    job, _prepared = _cancelled_with_upload(env)
    s3 = env["s3"]
    other_same_key = s3.create_multipart_upload(Bucket=VERSIONED, Key=DEST_KEY)["UploadId"]
    other_longer_key = s3.create_multipart_upload(Bucket=VERSIONED, Key=DEST_KEY + ".v2")["UploadId"]
    result = engine.sweep({"jobId": job["jobId"]}, final=True)
    assert result == {"swept": True, "listed": True, "remaining": False}
    listed = {(u["Key"], u["UploadId"])
              for u in s3.list_multipart_uploads(Bucket=VERSIONED).get("Uploads", [])}
    assert listed == {(DEST_KEY, other_same_key), (DEST_KEY + ".v2", other_longer_key)}


def test_the_sweep_leaves_a_succeeded_job_alone(env, machine):
    job, _prepared = _start(env, VERSIONED)
    loc.update_job_row(env["jobs"], job["jobId"], {"status": loc.STATUS_SUCCEEDED})
    with patch.object(env["s3"], "abort_multipart_upload") as abort:
        result = engine.sweep({"jobId": job["jobId"]})
    abort.assert_not_called()
    assert result["swept"] is False
    assert len(_uploads(env, VERSIONED)) == 1


def test_the_sweep_leaves_a_running_copy_alone(env, machine):
    """An open job with no stop recorded is a copy still running: never aborted."""
    job, _prepared = _start(env, VERSIONED)
    with patch.object(env["s3"], "abort_multipart_upload") as abort:
        result = engine.sweep({"jobId": job["jobId"]})
    abort.assert_not_called()
    assert result["swept"] is False


def test_the_sweep_aborts_an_open_job_whose_stop_was_recorded(env, machine):
    job, _prepared = _start(env, VERSIONED)
    assert loc.request_stop(env["jobs"], job["jobId"])
    assert engine.sweep({"jobId": job["jobId"]})["swept"] is True
    assert _uploads(env, VERSIONED) == []
    # The decision stays with the cancel path and the reconcile.
    assert loc.is_open_job(_row(env, job))


def test_the_landed_object_is_decided_first_and_never_touched(env, machine):
    """A CANCELLED row whose own object is at the key (its Complete landed after the cancel and has
    not superseded the row yet): no abort, the object stays."""
    job, prepared = _start(env, VERSIONED)
    for batch in prepared["batches"][1:]:
        engine.copy_batch(prepared["job"], batch)
    engine.complete(prepared["job"])
    loc.update_job_row(env["jobs"], job["jobId"], {"status": loc.STATUS_CANCELLED})
    with patch.object(env["s3"], "abort_multipart_upload") as abort, \
            patch.object(env["s3"], "list_multipart_uploads") as listing:
        result = engine.sweep({"jobId": job["jobId"]}, final=True)
    abort.assert_not_called()
    listing.assert_not_called()
    assert result == {"swept": False, "reason": "the job's object landed"}
    env["s3"].head_object(Bucket=VERSIONED, Key=DEST_KEY)


def test_a_landed_check_that_fails_raises_so_the_pass_is_retried(env, machine):
    job, _prepared = _cancelled_with_upload(env)
    denied = ClientError({"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject")
    with patch.object(env["s3"], "head_object", side_effect=denied), \
            patch.object(env["s3"], "abort_multipart_upload") as abort:
        with pytest.raises(ClientError):
            engine.sweep({"jobId": job["jobId"]})
    abort.assert_not_called()


def test_an_upload_not_listed_is_not_aborted(env, machine):
    job, _prepared = _cancelled_with_upload(env)
    upload_id = _row(env, job)["uploadIdS3"]
    env["s3"].abort_multipart_upload(Bucket=VERSIONED, Key=DEST_KEY, UploadId=upload_id)
    with patch.object(env["s3"], "abort_multipart_upload") as abort:
        result = engine.sweep({"jobId": job["jobId"]})
    abort.assert_not_called()
    assert result == {"swept": True, "listed": False, "remaining": False}


def test_the_listing_follows_pages_and_is_bounded():
    pages = [
        {"Uploads": [{"Key": DEST_KEY, "UploadId": "other"}], "IsTruncated": True,
         "NextKeyMarker": DEST_KEY, "NextUploadIdMarker": "other"},
        {"Uploads": [{"Key": DEST_KEY, "UploadId": "mine"}], "IsTruncated": False},
    ]
    client = MagicMock(**{"list_multipart_uploads.side_effect": pages})
    assert loc.recorded_upload_listed(client, "b", DEST_KEY, "mine") is True
    second = client.list_multipart_uploads.call_args_list[1].kwargs
    assert second == {"Bucket": "b", "Prefix": DEST_KEY, "KeyMarker": DEST_KEY,
                      "UploadIdMarker": "other"}
    endless = MagicMock(**{"list_multipart_uploads.return_value": {
        "Uploads": [], "IsTruncated": True, "NextKeyMarker": "k", "NextUploadIdMarker": "u"}})
    assert loc.recorded_upload_listed(endless, "b", DEST_KEY, "mine", max_pages=3) is False
    assert endless.list_multipart_uploads.call_count == 3


def test_the_sweep_signs_for_the_rows_region(env, machine):
    job, _prepared = _cancelled_with_upload(env)
    loc.update_job_row(env["jobs"], job["jobId"], {"destRegion": "eu-west-1"})
    with patch.object(engine, "register_bucket_region") as register:
        engine.sweep({"jobId": job["jobId"]})
    register.assert_called_with(VERSIONED, "eu-west-1")


def test_a_sweep_request_without_a_job_id_is_refused(env, machine):
    with pytest.raises(ValueError):
        engine.lambda_handler({"action": "sweep", "sweep": {}}, None)
    assert engine.sweep({"jobId": "no-such-job"}) == {"swept": False, "reason": "no job row"}


# --------------------------------------------------------------------------------------------- #
# Scheduling
# --------------------------------------------------------------------------------------------- #

def test_scheduling_is_once_per_job_and_best_effort(machine):
    sfn = MagicMock()
    assert loc.schedule_delayed_sweep(sfn, "j1") is True
    kwargs = sfn.start_execution.call_args.kwargs
    assert kwargs == {"stateMachineArn": MACHINE_ARN, "name": loc.sweep_execution_name_for("j1"),
                      "input": json.dumps({"sweep": {"jobId": "j1"}})}
    assert len(kwargs["name"]) <= 80
    assert kwargs["name"] != loc.execution_name_for("j1")
    sfn.start_execution.side_effect = ClientError(
        {"Error": {"Code": "ExecutionAlreadyExists", "Message": "x"}}, "StartExecution")
    assert loc.schedule_delayed_sweep(sfn, "j1") is True
    sfn.start_execution.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "x"}}, "StartExecution")
    assert loc.schedule_delayed_sweep(sfn, "j1") is False
    sfn.start_execution.side_effect = RuntimeError("down")
    assert loc.schedule_delayed_sweep(sfn, "j1") is False


def test_no_sweep_is_scheduled_without_a_state_machine():
    sfn = MagicMock()
    with patch.dict("os.environ", {loc.ENV_STATE_MACHINE_ARN: ""}):
        assert loc.schedule_delayed_sweep(sfn, "j1") is False
    sfn.start_execution.assert_not_called()


def test_a_cancel_whose_sweep_cannot_be_scheduled_still_cancels(env, machine):
    job, _prepared = _start(env, VERSIONED)
    env["sfn"].start_execution.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "x"}}, "StartExecution")
    assert loc.cancel_execution_copy_jobs(env["jobs"], env["sfn"], env["s3"], "e1") == []
    assert _row(env, job)["status"] == loc.STATUS_CANCELLED


def test_a_sweep_execution_that_ended_unfinished_is_not_reconciled(env, machine):
    env["sfn"].describe_execution.return_value = {
        "executionArn": EXEC_ARN, "status": "FAILED", "error": "States.TaskFailed",
        "input": loc.sweep_input("j1")}
    assert engine.reconcile(EXEC_ARN) == {"reconciled": False}
