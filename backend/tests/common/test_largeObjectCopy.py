# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The large object copy engine's shared logic: the part planner at the sizes the engine is built for,
job identity, settings bounds, the job and batch rows against moto's DynamoDB (exactly-once counting
and notification, in both orders of token registration and job completion), cancellation of an
execution's copies, and the read-path hold for a run still ingesting outputs."""

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from backend.backend.common import largeObjectCopy as loc

REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
TABLE = "t-copy-jobs"
GIB = loc.GIB
TIB = loc.TIB


# --------------------------------------------------------------------------------------------- #
# Part planner
# --------------------------------------------------------------------------------------------- #

@pytest.mark.parametrize("size", [
    GIB + 1, 5 * GIB, 50 * GIB, TIB, 5 * TIB - 1, 5 * TIB,
])
def test_plan_covers_the_object_within_s3_limits(size):
    part_size, part_count = loc.plan_parts(size, 256 * loc.MIB)
    assert loc.S3_MIN_PART_BYTES <= part_size <= loc.S3_MAX_PART_BYTES
    assert part_size % loc.MIB == 0
    assert part_count <= loc.MAX_PLANNED_PARTS
    # The parts cover the object exactly, and only the last part may be short.
    first, last = loc.part_range(part_count, part_size, size)
    assert last == size - 1
    assert first == (part_count - 1) * part_size
    assert last - first + 1 <= part_size


def test_plan_refuses_an_object_over_the_s3_limit():
    with pytest.raises(loc.CopyRefused):
        loc.plan_parts(5 * TIB + 1)


def test_plan_refuses_an_empty_object():
    with pytest.raises(ValueError):
        loc.plan_parts(0)


def test_five_tib_uses_wider_parts_than_the_minimum():
    part_size, part_count = loc.plan_parts(5 * TIB, 256 * loc.MIB)
    assert part_size > 256 * loc.MIB
    assert part_count <= loc.MAX_PLANNED_PARTS


@pytest.mark.parametrize("part_count,per_batch", [(1, 16), (16, 16), (17, 16), (4100, 16), (9000, 1)])
def test_batches_are_contiguous_and_bounded(part_count, per_batch):
    batches = loc.plan_batches(part_count, per_batch)
    assert len(batches) <= loc.MAX_BATCHES
    assert batches[0]["startPart"] == 1
    assert batches[-1]["endPart"] == part_count
    for left, right in zip(batches, batches[1:]):
        assert right["startPart"] == left["endPart"] + 1


# --------------------------------------------------------------------------------------------- #
# Identity and settings
# --------------------------------------------------------------------------------------------- #

def test_job_id_is_deterministic_per_scope_and_file():
    assert loc.job_id_for("exec-1", "a/b.bin") == loc.job_id_for("exec-1", "a/b.bin")
    assert loc.job_id_for("exec-1", "a/b.bin") != loc.job_id_for("exec-1", "a/c.bin")
    assert loc.job_id_for("exec-1", "a/b.bin") != loc.job_id_for("exec-2", "a/b.bin")


def test_execution_name_fits_step_functions_rules():
    name = loc.execution_name_for(loc.job_id_for("x" * 200, "y" * 1000))
    assert len(name) <= 80
    assert all(c.isalnum() or c in "-_" for c in name)


def test_settings_are_the_fixed_constants():
    assert loc.settings() == {
        "apiUploadEngineThresholdBytes": 5 * GIB, "minPartSizeBytes": 256 * loc.MIB,
        "partsPerBatch": 16, "threadsPerBatch": 8, "maxOutputIngestionWaitHours": 48,
        "batchesPerChunk": loc.DEFAULT_BATCHES_PER_CHUNK}
    assert loc.MAX_CONCURRENT_BATCHES == 10
    assert loc.DEFAULT_BATCHES_PER_CHUNK == 50


def test_settings_ignore_the_removed_tuning_environment(monkeypatch):
    """The engine settings are no longer deployment configuration: a function that still carries
    an old tuning variable (a stale environment) keeps the fixed values."""
    for name, value in (("LARGE_OBJECT_COPY_MAX_WAIT_HOURS", "2"),
                        ("LARGE_OBJECT_COPY_API_UPLOAD_THRESHOLD_BYTES", str(2 * GIB)),
                        ("LARGE_OBJECT_COPY_PARTS_PER_BATCH", "1"),
                        ("LARGE_OBJECT_COPY_BATCHES_PER_CHUNK", "20")):
        monkeypatch.setenv(name, value)
    assert loc.settings()["maxOutputIngestionWaitHours"] == 48
    assert loc.settings()["apiUploadEngineThresholdBytes"] == 5 * GIB
    assert loc.settings()["partsPerBatch"] == 16
    assert loc.settings()["batchesPerChunk"] == loc.DEFAULT_BATCHES_PER_CHUNK


def test_the_constants_keep_the_engine_within_amazon_s3_limits():
    assert loc.LARGE_FILE_THRESHOLD_BYTES <= loc.API_UPLOAD_ENGINE_THRESHOLD_BYTES <= loc.S3_MAX_OBJECT_BYTES
    assert loc.S3_MIN_PART_BYTES <= loc.MIN_PART_SIZE_BYTES <= loc.S3_MAX_PART_BYTES
    assert 1 <= loc.THREADS_PER_BATCH <= loc.PARTS_PER_BATCH


@pytest.mark.parametrize("size,large", [
    (loc.LARGE_FILE_THRESHOLD_BYTES, False), (loc.LARGE_FILE_THRESHOLD_BYTES + 1, True),
    (None, False), ("bad", False), (0, False)])
def test_is_large_matches_the_async_threshold(size, large):
    assert loc.is_large(size) is large


def test_threshold_is_unchanged_from_the_shipped_value():
    # Files at or below 1 GiB keep the synchronous path; changing this moves files between paths.
    assert loc.LARGE_FILE_THRESHOLD_BYTES == 1024 ** 3


# --------------------------------------------------------------------------------------------- #
# Rows against moto
# --------------------------------------------------------------------------------------------- #

def _create_table(ddb):
    table = ddb.create_table(
        TableName=TABLE,
        KeySchema=[{"AttributeName": "jobId", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "jobId", "AttributeType": "S"},
                              {"AttributeName": "workflowExecutionId", "AttributeType": "S"}],
        GlobalSecondaryIndexes=[{
            "IndexName": loc.WORKFLOW_EXECUTION_ID_INDEX,
            "KeySchema": [{"AttributeName": "workflowExecutionId", "KeyType": "HASH"}],
            "Projection": {"ProjectionType": "ALL"},
        }],
        BillingMode="PAY_PER_REQUEST",
    )
    table.wait_until_exists()
    return table


def _job(relative_key="out/big.bin", batch_id="exec-1"):
    return loc.build_job(
        kind=loc.KIND_WORKFLOW_OUTPUT,
        source={"bucket": "run", "key": f"pipelines/x/{relative_key}", "size": 2 * GIB},
        dest={"bucket": "asset", "key": f"a1/{relative_key}"},
        metadata={"assetid": "a1"}, content_headers={}, database_id="db", asset_id="a1",
        upload_id="y1", upload_type="assetFile", relative_key=relative_key, change_user_id="u1",
        scope_id=batch_id, workflow_execution_id=batch_id, batch_id=batch_id)


@pytest.fixture
def table():
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name=REGION)
        yield _create_table(ddb), boto3.client("dynamodb", region_name=REGION)


def test_start_copy_job_records_and_starts_once(table):
    tbl, _client = table
    sfn = MagicMock()
    sfn.start_execution.return_value = {"executionArn": "arn:aws:states:r:1:execution:sm:loc-1"}
    job = _job()
    loc.start_copy_job(tbl, sfn, "arn:aws:states:r:1:stateMachine:sm", job)
    # A repeated start (a retried output step) neither fails nor records twice.
    sfn.start_execution.side_effect = ClientError(
        {"Error": {"Code": "ExecutionAlreadyExists"}}, "StartExecution")
    loc.start_copy_job(tbl, sfn, "arn:aws:states:r:1:stateMachine:sm", job)
    row = loc.get_job_row(tbl, job["jobId"])
    assert row["status"] == loc.STATUS_QUEUED
    assert row["workflowExecutionId"] == "exec-1"
    assert row["executionArn"].endswith(loc.execution_name_for(job["jobId"]))
    assert sfn.start_execution.call_args.kwargs["name"] == loc.execution_name_for(job["jobId"])


def test_start_copy_job_without_a_state_machine_raises(table):
    tbl, _client = table
    with pytest.raises(RuntimeError):
        loc.start_copy_job(tbl, MagicMock(), "", _job())


def test_start_failure_other_than_already_exists_raises(table):
    tbl, _client = table
    sfn = MagicMock()
    sfn.start_execution.side_effect = ClientError({"Error": {"Code": "AccessDeniedException"}},
                                                  "StartExecution")
    with pytest.raises(ClientError):
        loc.start_copy_job(tbl, sfn, "arn:aws:states:r:1:stateMachine:sm", _job())


def _seed_jobs(tbl, count, batch_id="exec-1"):
    jobs = [_job(f"out/f{i}.bin", batch_id) for i in range(count)]
    sfn = MagicMock()
    sfn.start_execution.return_value = {"executionArn": "arn"}
    for job in jobs:
        loc.start_copy_job(tbl, sfn, "arn:aws:states:r:1:stateMachine:sm", job)
    return jobs


def test_a_job_is_counted_once_however_often_it_notifies(table):
    tbl, client = table
    jobs = _seed_jobs(tbl, 2)
    loc.record_batch(tbl, "exec-1", [j["jobId"] for j in jobs], {"workflowExecutionId": "exec-1", "legacy": False})
    assert loc.count_job_terminal(client, TABLE, jobs[0]["jobId"], "exec-1", loc.STATUS_SUCCEEDED)
    assert not loc.count_job_terminal(client, TABLE, jobs[0]["jobId"], "exec-1", loc.STATUS_SUCCEEDED)
    assert loc.get_batch(tbl, "exec-1")["terminalCount"] == 1


def test_notification_waits_for_every_job_and_the_token(table):
    tbl, client = table
    jobs = _seed_jobs(tbl, 2)
    loc.record_batch(tbl, "exec-1", [j["jobId"] for j in jobs], {"workflowExecutionId": "exec-1", "legacy": False})
    loc.count_job_terminal(client, TABLE, jobs[0]["jobId"], "exec-1", loc.STATUS_SUCCEEDED)
    loc.register_task_token(tbl, "exec-1", "token-1")
    assert loc.claim_notification(tbl, "exec-1") is None  # one job still running
    loc.count_job_terminal(client, TABLE, jobs[1]["jobId"], "exec-1", loc.STATUS_FAILED)
    claimed = loc.claim_notification(tbl, "exec-1")
    assert claimed and claimed["taskToken"] == "token-1"
    assert loc.claim_notification(tbl, "exec-1") is None  # exactly once


def test_jobs_finishing_before_the_token_is_registered_are_reported_by_the_waiter(table):
    tbl, client = table
    jobs = _seed_jobs(tbl, 1)
    # The job finishes first: the batch row does not exist yet, the count creates it.
    loc.count_job_terminal(client, TABLE, jobs[0]["jobId"], "exec-1", loc.STATUS_SUCCEEDED)
    assert loc.claim_notification(tbl, "exec-1") is None  # no total yet
    loc.record_batch(tbl, "exec-1", [j["jobId"] for j in jobs], {"workflowExecutionId": "exec-1", "legacy": False})
    # The count survives the batch being recorded afterwards.
    assert loc.get_batch(tbl, "exec-1")["terminalCount"] == 1
    assert loc.claim_notification(tbl, "exec-1") is None  # no token yet
    loc.register_task_token(tbl, "exec-1", "token-1")
    assert loc.claim_notification(tbl, "exec-1")["taskToken"] == "token-1"


def test_a_legacy_batch_is_reported_without_a_token(table):
    tbl, client = table
    jobs = _seed_jobs(tbl, 1)
    loc.record_batch(tbl, "exec-1", [j["jobId"] for j in jobs], {"workflowExecutionId": "exec-1", "legacy": True})
    loc.count_job_terminal(client, TABLE, jobs[0]["jobId"], "exec-1", loc.STATUS_SUCCEEDED)
    assert loc.claim_notification(tbl, "exec-1") is not None


def test_a_retried_record_keeps_the_jobs_an_earlier_attempt_recorded(table):
    """[in-dev D5] Attempt 1 records A, B and C; A finishes; attempt 2 no longer lists A (its staged
    source is deleted) and records only B and C. The total stays 3, so the batch is not claimed when
    B finishes while C is still copying."""
    tbl, client = table
    jobs = _seed_jobs(tbl, 3)
    ids = [j["jobId"] for j in jobs]
    ctx = {"workflowExecutionId": "exec-1", "legacy": False}
    loc.record_batch(tbl, "exec-1", ids, ctx)
    loc.register_task_token(tbl, "exec-1", "token-1")
    loc.count_job_terminal(client, TABLE, ids[0], "exec-1", loc.STATUS_SUCCEEDED)
    row = loc.record_batch(tbl, "exec-1", ids[1:], ctx)
    assert row["totalCount"] == 3
    loc.count_job_terminal(client, TABLE, ids[1], "exec-1", loc.STATUS_SUCCEEDED)
    assert loc.claim_notification(tbl, "exec-1") is None  # C is still copying
    loc.count_job_terminal(client, TABLE, ids[2], "exec-1", loc.STATUS_SUCCEEDED)
    assert loc.claim_notification(tbl, "exec-1") is not None


def test_the_batch_total_never_shrinks(table):
    tbl, _client = table
    ctx = {"workflowExecutionId": "exec-1", "legacy": False}
    loc.record_batch(tbl, "exec-1", ["a", "b", "c"], ctx)
    assert loc.record_batch(tbl, "exec-1", ["b"], ctx)["totalCount"] == 3
    assert loc.record_batch(tbl, "exec-1", ["d"], ctx)["totalCount"] == 4


def test_recorded_batch_jobs_lists_every_attempt_s_jobs(table):
    tbl, _client = table
    jobs = _seed_jobs(tbl, 2)
    _seed_jobs(tbl, 1, batch_id="exec-2")
    assert set(loc.recorded_batch_jobs(tbl, "exec-1", "exec-1")) == {j["jobId"] for j in jobs}


def test_batch_outcomes_are_per_relative_key_and_scoped_to_the_batch(table):
    tbl, client = table
    jobs = _seed_jobs(tbl, 2)
    other = _seed_jobs(tbl, 1, batch_id="exec-2")
    loc.count_job_terminal(client, TABLE, jobs[0]["jobId"], "exec-1", loc.STATUS_SUCCEEDED)
    loc.update_job_row(tbl, jobs[1]["jobId"], {"status": loc.STATUS_FAILED, "error": "x"})
    outcomes = loc.batch_outcomes(tbl, "exec-1", "exec-1")
    assert set(outcomes) == {"out/f0.bin", "out/f1.bin"}
    assert outcomes["out/f0.bin"]["status"] == loc.STATUS_SUCCEEDED
    assert outcomes["out/f1.bin"]["status"] == loc.STATUS_FAILED
    assert other[0]["relativeKey"] not in loc.batch_outcomes(tbl, "exec-1", "exec-2")


def test_delivering_to_a_token_the_workflow_no_longer_accepts_is_not_an_error(table):
    tbl, _client = table
    sfn = MagicMock()
    sfn.send_task_success.side_effect = ClientError({"Error": {"Code": "TaskTimedOut"}},
                                                    "SendTaskSuccess")
    assert loc.deliver_batch_report(tbl, {"jobId": "batch#exec-1", "taskToken": "t"}, sfn) is False


def test_a_failed_delivery_releases_the_claim_for_a_retry(table):
    tbl, client = table
    jobs = _seed_jobs(tbl, 1)
    loc.record_batch(tbl, "exec-1", [j["jobId"] for j in jobs], {"workflowExecutionId": "exec-1", "legacy": False})
    loc.register_task_token(tbl, "exec-1", "t")
    loc.count_job_terminal(client, TABLE, jobs[0]["jobId"], "exec-1", loc.STATUS_SUCCEEDED)
    claimed = loc.claim_notification(tbl, "exec-1")
    sfn = MagicMock()
    sfn.send_task_success.side_effect = ClientError({"Error": {"Code": "ThrottlingException"}},
                                                    "SendTaskSuccess")
    with pytest.raises(ClientError):
        loc.deliver_batch_report(tbl, claimed, sfn)
    assert loc.claim_notification(tbl, "exec-1") is not None


def test_a_legacy_report_invokes_the_output_step_finalize(table):
    tbl, _client = table
    lam = MagicMock()
    assert loc.deliver_batch_report(
        tbl, {"jobId": "batch#exec-1", "context": {"workflowExecutionId": "exec-1"}}, MagicMock(),
        lam, "process-output")
    kwargs = lam.invoke.call_args.kwargs
    assert kwargs["FunctionName"] == "process-output"
    assert kwargs["InvocationType"] == "Event"
    assert '"mode": "finalize"' in kwargs["Payload"]


def test_cancel_stops_unfinished_jobs_and_aborts_their_uploads(table):
    tbl, client = table
    jobs = _seed_jobs(tbl, 3)
    loc.update_job_row(tbl, jobs[0]["jobId"], {"status": loc.STATUS_RUNNING, "uploadIdS3": "u0"})
    loc.update_job_row(tbl, jobs[1]["jobId"], {"status": loc.STATUS_SUCCEEDED, "uploadIdS3": "u1"})
    sfn, s3 = MagicMock(), MagicMock()
    warnings = loc.cancel_execution_copy_jobs(tbl, sfn, s3, "exec-1")
    assert warnings == []
    assert sfn.stop_execution.call_count == 2  # the running and the queued job, not the finished one
    s3.abort_multipart_upload.assert_called_once()
    assert s3.abort_multipart_upload.call_args.kwargs["UploadId"] == "u0"
    assert loc.get_job_row(tbl, jobs[0]["jobId"])["status"] == loc.STATUS_CANCELLED
    assert loc.get_job_row(tbl, jobs[1]["jobId"])["status"] == loc.STATUS_SUCCEEDED


def test_cancel_reports_a_copy_it_could_not_stop(table):
    tbl, _client = table
    _seed_jobs(tbl, 1)
    sfn = MagicMock()
    sfn.stop_execution.side_effect = ClientError({"Error": {"Code": "AccessDeniedException"}},
                                                 "StopExecution")
    warnings = loc.cancel_execution_copy_jobs(tbl, sfn, MagicMock(), "exec-1")
    assert len(warnings) == 1 and "out/f0.bin" in warnings[0]


def test_cancel_with_no_jobs_does_nothing(table):
    tbl, _client = table
    sfn = MagicMock()
    assert loc.cancel_execution_copy_jobs(tbl, sfn, MagicMock(), "exec-none") == []
    sfn.stop_execution.assert_not_called()


# --------------------------------------------------------------------------------------------- #
# Read-path hold
# --------------------------------------------------------------------------------------------- #

def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("main_item,status,expected", [
    ({}, "SUCCEEDED", None),  # a run that never handed outputs to copy jobs
    ({"executionPhase": "INGESTING_OUTPUTS", "outputIngestionStartedDate": _iso(NOW)}, "FAILED", None),
    ({"executionPhase": "INGESTING_OUTPUTS", "outputIngestionStartedDate": _iso(NOW)}, "ABORTED", None),
    ({"executionPhase": "INGESTING_OUTPUTS", "outputIngestionStartedDate": _iso(NOW)}, "SUCCEEDED", "hold"),
    ({"executionPhase": "INGESTING_OUTPUTS",
      "outputIngestionStartedDate": _iso(NOW - timedelta(hours=48, minutes=59))}, "SUCCEEDED", "hold"),
    ({"executionPhase": "INGESTING_OUTPUTS",
      "outputIngestionStartedDate": _iso(NOW - timedelta(hours=49, minutes=1))}, "SUCCEEDED", "expired"),
    ({"executionPhase": "INGESTING_OUTPUTS"}, "SUCCEEDED", "expired"),
])
def test_ingestion_hold(main_item, status, expected):
    assert loc.ingestion_hold(main_item, status, max_wait_hours=48, now=NOW) == expected


# --------------------------------------------------------------------------------------------- #
# Round 2 (#1123 H2): chunks and the ended-execution check of the details read
# --------------------------------------------------------------------------------------------- #

def test_the_largest_plan_splits_into_chunks_of_the_configured_size():
    batches = loc.plan_batches(loc.MAX_BATCHES * 4, 1)
    assert len(batches) == loc.MAX_BATCHES
    chunks = loc.plan_chunks(batches, loc.DEFAULT_BATCHES_PER_CHUNK)
    assert len(chunks) == loc.MAX_BATCHES // loc.DEFAULT_BATCHES_PER_CHUNK
    assert all(len(c["batches"]) <= loc.DEFAULT_BATCHES_PER_CHUNK for c in chunks)
    assert [b for c in chunks for b in c["batches"]] == batches  # order and coverage kept


def test_a_small_plan_is_one_chunk():
    assert loc.plan_chunks([{"startPart": 1, "endPart": 3}]) == [{"batches": [{"startPart": 1, "endPart": 3}]}]
    assert loc.plan_chunks([]) == []


def _sfn(statuses):
    client = MagicMock()
    client.describe_execution.side_effect = lambda executionArn: statuses[executionArn]
    return client


def test_only_unfinished_jobs_whose_execution_ended_unreported_are_returned():
    rows = [
        {"jobId": "a", "status": "RUNNING", "executionArn": "x:a"},
        {"jobId": "b", "status": "SUCCEEDED", "executionArn": "x:b"},
        {"jobId": "c", "status": "QUEUED", "executionArn": "x:c"},
        {"jobId": "d", "status": "RUNNING", "executionArn": "x:d"},
        {"jobId": "e", "status": "RUNNING"},
    ]
    client = _sfn({"x:a": {"status": "FAILED"}, "x:c": {"status": "RUNNING"},
                   "x:d": {"status": "ABORTED", "error": loc.STOP_ERROR_CANCELLED}})
    # x:d: an open job of a stop the cancel path made (left open for the engine; #1123 M11)
    assert loc.ended_copy_executions(client, rows) == ["x:a", "x:d"]
    described = [c.kwargs["executionArn"] for c in client.describe_execution.call_args_list]
    assert described == ["x:a", "x:c", "x:d"]  # terminal rows and rows without an execution skipped


def test_the_ended_check_is_bounded_per_read():
    rows = [{"jobId": str(i), "status": "RUNNING", "executionArn": f"x:{i}"} for i in range(50)]
    client = MagicMock()
    client.describe_execution.return_value = {"status": "TIMED_OUT"}
    assert len(loc.ended_copy_executions(client, rows)) == loc.MAX_RECONCILE_CHECKS_PER_READ
    assert client.describe_execution.call_count == loc.MAX_RECONCILE_CHECKS_PER_READ


def test_a_failed_describe_is_skipped():
    client = MagicMock()
    client.describe_execution.side_effect = RuntimeError("throttled")
    assert loc.ended_copy_executions(client, [{"jobId": "a", "status": "RUNNING", "executionArn": "x"}]) == []


def test_the_cancel_path_stops_with_the_error_the_reconcile_skips():
    table, sfn, s3 = MagicMock(), MagicMock(), MagicMock()
    table.query.return_value = {"Items": [{"jobId": "j", "status": "RUNNING", "executionArn": "arn"}]}
    loc.cancel_execution_copy_jobs(table, sfn, s3, "e1")
    assert sfn.stop_execution.call_args.kwargs["error"] == loc.STOP_ERROR_CANCELLED
    assert not loc.is_unreported_end({"status": "ABORTED", "error": loc.STOP_ERROR_CANCELLED})
    assert loc.is_unreported_end({"status": "ABORTED"})
    assert loc.is_cancelled_stop({"status": "ABORTED", "error": loc.STOP_ERROR_CANCELLED})
    assert not loc.is_cancelled_stop({"status": "ABORTED"})
    assert not loc.is_cancelled_stop({"status": "FAILED", "error": loc.STOP_ERROR_CANCELLED})
