# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Round 2 of #1123 (H2, L7, L9): the copy engine's chunked plan, the refusals a child execution
reports, the streamed part cap, and the reconcile of a copy execution that ended outside its states.

A Standard execution that hits its 25,000-event history quota, fails at the execution level, times
out, or is stopped in the console runs no Catch, Fail or notify state. Before this change its job row
stayed RUNNING, so the waiting run sat for maxOutputIngestionWaitHours, an API upload's file stayed
PENDING and the multipart upload stayed open. The reconcile finalizes such a job exactly once."""

import json
import os
from unittest.mock import patch

import pytest

from backend.tests.handlers.assets.test_largeObjectCopy_engine import (  # noqa: F401 (fixture)
    SMALL_PARTS, _job, _run_copy, _uploads_table, engine, env, loc)

MACHINE_ARN = "arn:aws:states:r:1:stateMachine:LargeObjectCopy-x"
EXEC_ARN = "arn:aws:states:r:1:execution:LargeObjectCopy-x:loc-abc"
GIB = loc.GIB


@pytest.fixture
def machine():
    with patch.dict(os.environ, {loc.ENV_STATE_MACHINE_ARN: MACHINE_ARN}):
        yield


def _described(job, status="FAILED", error="States.Runtime"):
    return {"executionArn": EXEC_ARN, "status": status, "error": error,
            "input": json.dumps({"job": job})}


def _job_with_execution(env, **kw):
    job = _job(**kw)
    loc.update_job_row(env["jobs"], job["jobId"], {"executionArn": EXEC_ARN})
    return job


def _waiting_batch(env, job):
    loc.record_batch(env["jobs"], "e1", [job["jobId"]], {"workflowExecutionId": "e1", "legacy": False})
    loc.register_task_token(env["jobs"], "e1", "token-1")


# --------------------------------------------------------------------------------------------- #
# Chunked plan (H2: each chunk is a child execution with its own history)
# --------------------------------------------------------------------------------------------- #

def test_prepare_groups_the_batches_in_chunks_and_keeps_the_batch_list(env):
    prepared = engine.prepare(_job())
    # 3 parts, 2 per batch -> 2 batches; 1 batch per chunk -> 2 chunks
    assert prepared["batches"] == [{"startPart": 1, "endPart": 2}, {"startPart": 3, "endPart": 3}]
    assert prepared["chunks"] == [{"batches": [b]} for b in prepared["batches"]]


def test_a_copy_driven_chunk_by_chunk_lands_the_object(env):
    job = _job()
    prepared = engine.prepare(job)
    for chunk in prepared["chunks"]:
        for batch in chunk["batches"]:
            engine.copy_batch(prepared["job"], batch)
    engine.complete(prepared["job"])
    assert loc.get_job_row(env["jobs"], job["jobId"])["status"] == loc.STATUS_SUCCEEDED


def test_the_chunk_size_is_the_fixed_history_bound():
    # The infra test pins DEFAULT_BATCHES_PER_CHUNK to the construct's BATCHES_PER_CHUNK.
    assert loc.settings()["batchesPerChunk"] == loc.DEFAULT_BATCHES_PER_CHUNK == 50


# --------------------------------------------------------------------------------------------- #
# A refusal inside a child execution reaches the job (wrapped error) with its reason
# --------------------------------------------------------------------------------------------- #

CHILD_FAILED = {"Error": "States.TaskFailed",
                "Cause": json.dumps({"executionArn": "child", "status": "FAILED"})}


def test_a_source_change_in_a_child_execution_fails_the_job_as_source_changed(env):
    prepared = engine.prepare(_job())
    source = prepared["job"]["source"]
    env["s3"].delete_object(Bucket=source["bucket"], Key=source["key"], VersionId=source["versionId"])
    with pytest.raises(loc.SourceChanged):
        engine.copy_batch(prepared["job"], prepared["batches"][0])
    result = engine.fail(prepared["job"], CHILD_FAILED)
    assert loc.get_job_row(env["jobs"], prepared["job"]["jobId"])["error"] == loc.REASON_SOURCE_CHANGED
    assert result["refused"] is True


def test_the_child_execution_error_in_the_cause_is_unwrapped():
    wrapped = {"Error": "States.TaskFailed",
               "Cause": json.dumps({"Error": "SourceChanged", "Cause": "{}", "status": "FAILED"})}
    assert engine._failure_reason(wrapped) == loc.REASON_SOURCE_CHANGED


def test_a_lambda_error_cause_is_not_mistaken_for_a_child_execution():
    error = {"Error": "Lambda.Unknown", "Cause": json.dumps({"errorMessage": "boom", "errorType": "X"})}
    assert engine._failure_reason(error) == loc.REASON_COPY_FAILED


def test_an_operational_failure_is_not_a_refusal(env):
    prepared = engine.prepare(_job())
    assert engine.fail(prepared["job"], CHILD_FAILED)["refused"] is False


@pytest.mark.parametrize("reason", loc.REFUSAL_REASONS)
def test_every_refusal_reason_ends_in_the_refused_state(env, reason):
    job = _job()
    loc.update_job_row(env["jobs"], job["jobId"], {"batchError": {"Error": "CopyRefused", "reason": reason}})
    assert engine.fail(job, CHILD_FAILED)["refused"] is True


# --------------------------------------------------------------------------------------------- #
# L7: streamed parts are capped
# --------------------------------------------------------------------------------------------- #

def test_a_streamed_copy_caps_the_part_size(env):
    big_parts = {**SMALL_PARTS, "minPartSizeBytes": 5 * GIB}
    with patch.object(loc, "settings", lambda environ=None: dict(big_parts)):
        server_side = engine.prepare(_job(batch_id="s1"))["job"]["partSize"]
        with patch.object(engine, "_streams_parts", lambda job: True):
            streamed = engine.prepare(_job(batch_id="s2"))["job"]["partSize"]
    assert server_side == 5 * GIB
    assert streamed == loc.STREAMED_MAX_PART_BYTES
    # Two streamed parts in memory stay under the in-VPC engine memory (5,308 MB)
    assert engine.STREAMED_COPY_THREADS * streamed < 5308 * 1000 * 1000


def test_the_streamed_cap_still_fits_the_largest_object():
    part_size, part_count = loc.plan_parts(loc.S3_MAX_OBJECT_BYTES, loc.STREAMED_MAX_PART_BYTES)
    assert part_size <= loc.STREAMED_MAX_PART_BYTES and part_count <= loc.S3_MAX_PARTS


# --------------------------------------------------------------------------------------------- #
# H2: reconcile of a copy execution that ended outside its states
# --------------------------------------------------------------------------------------------- #

def test_a_job_whose_execution_hit_the_history_quota_is_finalized_once(env, machine):
    job = _job_with_execution(env)
    engine.prepare(job)  # RUNNING with a multipart upload
    _waiting_batch(env, job)
    env["sfn"].describe_execution.return_value = _described(job)
    event = {"detail-type": "Step Functions Execution Status Change",
             "detail": {"executionArn": EXEC_ARN, "status": "FAILED"}}

    assert engine.lambda_handler(event, None)["reconciled"] is True
    engine.lambda_handler(event, None)  # EventBridge delivers at least once

    row = loc.get_job_row(env["jobs"], job["jobId"])
    assert row["status"] == loc.STATUS_FAILED
    assert row["error"] == loc.REASON_COPY_FAILED
    assert row["reconciledStatus"] == "FAILED"
    assert row["counted"] is True
    assert env["s3"].list_multipart_uploads(Bucket="asset-bucket").get("Uploads", []) == []
    assert env["audit"].call_count == 1 and env["audit"].call_args.args[4] is True
    # The waiting run is told once, so it finalizes now instead of after its maximum wait
    env["sfn"].send_task_success.assert_called_once()


def test_two_concurrent_reconciles_clean_up_and_audit_once(env, machine):
    """Two deliveries that both read the job row while it was still RUNNING: only the one whose
    conditional write moves the row to FAILED cleans up and audits."""
    job = _job_with_execution(env)
    engine.prepare(job)
    _waiting_batch(env, job)
    env["sfn"].describe_execution.return_value = _described(job)
    stale = loc.get_job_row(env["jobs"], job["jobId"])  # RUNNING, as both deliveries read it
    engine.reconcile(EXEC_ARN)
    real_get = loc.get_job_row
    with patch.object(loc, "get_job_row",
                      lambda table, job_id: dict(stale) if job_id == job["jobId"] else real_get(table, job_id)):
        engine.reconcile(EXEC_ARN)
    assert env["audit"].call_count == 1
    assert loc.get_job_row(env["jobs"], job["jobId"])["status"] == loc.STATUS_FAILED
    env["sfn"].send_task_success.assert_called_once()


@pytest.mark.parametrize("status", ["TIMED_OUT", "ABORTED"])
def test_a_timed_out_or_console_stopped_execution_is_finalized(env, machine, status):
    job = _job_with_execution(env)
    _waiting_batch(env, job)
    env["sfn"].describe_execution.return_value = _described(job, status=status, error="")
    engine.reconcile(EXEC_ARN)
    assert loc.get_job_row(env["jobs"], job["jobId"])["status"] == loc.STATUS_FAILED
    env["sfn"].send_task_success.assert_called_once()


def test_a_stop_by_the_cancel_path_gets_the_cancel_paths_decision(env, machine):
    """Round 4 (D9): the reconcile of a stop the cancel path made writes the cancel path's own
    decision for a job still open (CANCELLED: nothing landed), never FAILED, and audits nothing."""
    job = _job_with_execution(env)
    env["sfn"].describe_execution.return_value = _described(job, status="ABORTED",
                                                             error=loc.STOP_ERROR_CANCELLED)
    assert engine.reconcile(EXEC_ARN) == {"reconciled": True, "status": loc.STATUS_CANCELLED}
    row = loc.get_job_row(env["jobs"], job["jobId"])
    assert row["status"] == loc.STATUS_CANCELLED and row["error"] == loc.REASON_CANCELLED
    assert "counted" not in row
    env["audit"].assert_not_called()
    env["sfn"].send_task_success.assert_not_called()


def test_a_cancelled_job_is_not_reconciled(env, machine):
    job = _job_with_execution(env)
    loc.update_job_row(env["jobs"], job["jobId"], {"status": loc.STATUS_CANCELLED})
    env["sfn"].describe_execution.return_value = _described(job, status="ABORTED", error="")
    assert engine.reconcile(EXEC_ARN) == {"reconciled": False}
    assert loc.get_job_row(env["jobs"], job["jobId"])["status"] == loc.STATUS_CANCELLED


def test_a_landed_file_whose_notify_was_lost_is_counted_as_succeeded(env, machine):
    """LargeObjectCopyNotifyLost: the file landed, but its notify states could not report it."""
    job = _job_with_execution(env)
    _run_copy(job)
    _waiting_batch(env, job)
    env["sfn"].describe_execution.return_value = _described(job, error="LargeObjectCopyNotifyFailed")
    assert engine.reconcile(EXEC_ARN)["status"] == loc.STATUS_SUCCEEDED
    row = loc.get_job_row(env["jobs"], job["jobId"])
    assert row["status"] == loc.STATUS_SUCCEEDED and row["counted"] is True
    env["sfn"].send_task_success.assert_called_once()
    assert env["audit"].call_count == 1  # only the completion entry


def test_an_api_upload_copy_records_failed_on_the_upload(env, machine):
    uploads = _uploads_table()
    with patch.object(engine, "upload_table", uploads):
        job = _job_with_execution(env, kind=loc.KIND_UPLOAD)
        engine.prepare(job)  # pins the source version on the job row; the execution input has none
        env["sfn"].describe_execution.return_value = _described(job)
        engine.reconcile(EXEC_ARN)
    entry = uploads.get_item(Key={"uploadId": "y1", "assetId": "a1"})["Item"]["asyncFiles"]["big.bin"]
    assert entry["status"] == "FAILED"
    # An upload's staged object is removed, as the Fail state does
    assert env["s3"].list_object_versions(Bucket="run-bucket", Prefix="pipelines/").get("Versions", []) == []


def test_an_execution_still_running_is_left_alone(env, machine):
    job = _job_with_execution(env)
    env["sfn"].describe_execution.return_value = _described(job, status="RUNNING", error="")
    assert engine.reconcile(EXEC_ARN) == {"reconciled": False}
    assert loc.get_job_row(env["jobs"], job["jobId"])["status"] == loc.STATUS_QUEUED


def test_an_execution_of_another_state_machine_is_not_described(env, machine):
    other = "arn:aws:states:r:1:execution:LargeObjectCopyParts-x:child"
    assert engine.lambda_handler({"action": "reconcile", "executionArn": other}, None) == {"reconciled": False}
    env["sfn"].describe_execution.assert_not_called()


def test_an_execution_of_an_older_job_attempt_does_not_override_the_row(env, machine):
    job = _job_with_execution(env)
    loc.update_job_row(env["jobs"], job["jobId"], {"executionArn": EXEC_ARN + "-newer"})
    env["sfn"].describe_execution.return_value = _described(job)
    assert engine.reconcile(EXEC_ARN) == {"reconciled": False}
