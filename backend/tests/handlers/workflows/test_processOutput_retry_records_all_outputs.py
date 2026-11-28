# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A retried process-output step records every output the run ingested, and writes each one once (#1271).

The process-output state is retried on States.Timeout and transient Lambda faults
(`workflowAslBuilder.create_retry_config`). Ingestion MOVES each output into the asset (copy, then
delete of the staged source), so a retried attempt lists only what is still staged. These tests run two
attempts of the real end-state step against the real upload handler, with the run bucket, the asset
bucket, the upload records and the output-files table (which holds the run's ingest ledger) in moto,
and check what the retry records and how many asset versions each output has:

- an attempt killed after its ingestion finished: the retry records the outputs it no longer finds
  staged (from the ledger), and writes none of them again;
- an attempt killed mid-move (an output's copy landed, its staged source was not yet deleted): the
  retry does not write a second version of it, and still records the outputs the first attempt moved;
- an upgrade: a first attempt that ran before the ledger existed leaves no ledger rows, and the retry
  still completes and writes no second version;
- a large output (over the copy engine threshold) is handed to the large object copy engine (#1123):
  across a retry or a takeover there is one copy job, one asset version and one record, its staged
  source stays until the copy lands, and the run is recorded by the finalize step once it has.

The copy engine is a fake with the engine's contract: a deterministic job id per (run, file), a
repeated start is a no-op, the job pins the staged version it was handed and deletes it once its copy
lands, and the finalize step reads every job's outcome.

A Lambda timeout ends the process rather than raising, so `_Killed` derives from BaseException: no
`except Exception` in either handler catches it, as nothing would catch a killed process.
"""

import io
import json
import os
import threading
import time
from contextlib import ExitStack
from decimal import Decimal
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

# Import-time table names of the two handlers, so this file also runs on its own (module-level seeding).
for _k, _v in {
    "WORKFLOW_STORAGE_TABLE_V2_NAME": "t-wf-v2",
    "WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME": "t-inputs",
    "WORKFLOW_EXECUTION_LOCKS_STORAGE_TABLE_NAME": "t-locks",
    "WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME": "t-config",
    "PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME": "t-of",
    "SEND_EMAIL_FUNCTION_NAME": "test-send-email-function",
    "PRESIGNED_URL_TIMEOUT_SECONDS": "3600",
}.items():
    os.environ.setdefault(_k, _v)

from backend.tests.handlers.workflows import test_processOutput_ingestion_outcome as base  # noqa: E402,F401
from backend.backend.handlers.workflows.sfn import processWorkflowExecutionOutput as po  # noqa: E402
from backend.backend.handlers.assets import uploadFile as uf  # noqa: E402
from backend.backend.common.workflows import executionOutputs as eo  # noqa: E402
from backend.backend.common.s3MetadataKeys import VAMS_CHANGE_WORKFLOW_EXECUTION_ID_METADATA_KEY  # noqa: E402
from backend.backend.common import largeObjectCopy as loc  # noqa: E402

REGION = "us-east-1"
RUN_BUCKET = "run-io-bucket-1271"
ASSET_BUCKET = "output-asset-bucket-1271"
EXEC = "0123456789abcdef0123456789abcdef"
FILES_PREFIX = f"pipelines/p1/JOB/output/{EXEC}/files/"
PREVIEW_PREFIX = f"pipelines/p1/JOB/output/{EXEC}/preview/"
DATABASE_ID = "db-01"
ASSET_ID = "asset-01"
SMALL = ("a.glb", "b.glb")
PREVIEW = "thumb.png"
LARGE = "big.e57"
THRESHOLD = 100


def repeat_safe_copy_on(s3):
    """The upload finalize copy (common.s3.copy_s3_object_repeat_safe, #1848) sent on this test's
    moto client, so every write of the run lands in the bucket the test reads back."""
    def copy(source_bucket, source_key, dest_bucket, dest_key, extra_args, write_token, source_size,
             source_version_id=None, **_ignored):
        return s3.copy_object(CopySource={"Bucket": source_bucket, "Key": source_key}, Bucket=dest_bucket,
                              Key=dest_key, **extra_args).get("VersionId")
    return copy


class _Killed(BaseException):
    """The Lambda runtime ending an attempt at its timeout."""


def _event(with_preview=False, attempt=0):
    body = {
        # $$.State.RetryCount of the process-output state (ASL v2+).
        "outputStepAttempt": attempt,
        "outputAssetId": ASSET_ID, "outputDatabaseId": DATABASE_ID, "workflowExecutionId": EXEC,
        "endStatePipelineExecutionId": "P1", "workflowDatabaseId": "GLOBAL", "workflowId": "wf1",
        "workflowExecutionS3InputOutputBucket": RUN_BUCKET,
        "executingUserName": "SYSTEM_USER", "executingRequestContext": {"http": {}},
        "filesPathKey": FILES_PREFIX,
        # An ASL v2+ definition waits on its large outputs (AwaitOutputIngestion + FinalizeOutputs).
        "awaitOutputIngestion": True,
    }
    if with_preview:
        body["previewPathKey"] = PREVIEW_PREFIX
    return {"body": body}


def _tables():
    ddb = boto3.resource("dynamodb", region_name=REGION)
    uploads = ddb.create_table(
        TableName="t-asset-uploads-1271",
        KeySchema=[{"AttributeName": "uploadId", "KeyType": "HASH"},
                   {"AttributeName": "assetId", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": "uploadId", "AttributeType": "S"},
                              {"AttributeName": "assetId", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST")
    ddb.create_table(
        TableName=po.pipeline_execution_output_files_table,
        KeySchema=[{"AttributeName": "pipelineExecutionId", "KeyType": "HASH"},
                   {"AttributeName": "fileType:relativeFilePath", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": "pipelineExecutionId", "AttributeType": "S"},
                              {"AttributeName": "fileType:relativeFilePath", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST")
    return ddb, uploads


def _stage(s3, with_preview=False, with_large=False, run_bucket_versioned=True):
    for bucket in (RUN_BUCKET, ASSET_BUCKET):
        s3.create_bucket(Bucket=bucket)
        if bucket == ASSET_BUCKET or run_bucket_versioned:
            s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    for name in SMALL:
        s3.put_object(Bucket=RUN_BUCKET, Key=FILES_PREFIX + name, Body=b"small",
                      ContentType="model/gltf-binary")
    if with_large:
        s3.put_object(Bucket=RUN_BUCKET, Key=FILES_PREFIX + LARGE, Body=b"x" * (THRESHOLD * 2),
                      ContentType="application/octet-stream")
    if with_preview:
        s3.put_object(Bucket=RUN_BUCKET, Key=PREVIEW_PREFIX + PREVIEW, Body=b"png",
                      ContentType="image/png")


def _listing(s3, prefix):
    contents = s3.list_objects_v2(Bucket=RUN_BUCKET, Prefix=prefix).get("Contents", [])
    return {"Contents": [dict(o) for o in contents]} if contents else {}


def _ctx(i, remaining_ms=900_000):
    """A Lambda context per attempt: its own request id and the time it has left (15 minutes)."""
    return MagicMock(aws_request_id=f"attempt-{i}",
                     **{"get_remaining_time_in_millis.return_value": remaining_ms})


def _refused():
    """The error an attempt raises instead of ingesting outputs another live attempt holds (absent on
    code without the ledger)."""
    return getattr(po, "EarlierIngestionInProgress", _Never)


class _Never(Exception):
    pass


class _FakeCopyEngine:
    """The large object copy engine as the output step sees it (uploadFile.start_output_copy_job and
    processWorkflowExecutionOutput._engine), in memory over the moto buckets."""

    def __init__(self, s3):
        self.s3 = s3
        self.jobs = {}
        self.starts = []
        self.batch = {}

    def start(self, file_info, head_response, change_metadata, batch_id, _workflow_database_id):
        # As uploadFile.start_output_copy_job: the job keys the output by its path without the leading '/'
        relative_key = file_info["relativeKey"].lstrip("/")
        job_id = loc.job_id_for(batch_id, relative_key)
        self.starts.append(job_id)
        self.jobs.setdefault(job_id, {
            "jobId": job_id, "relativeKey": relative_key, "status": loc.STATUS_QUEUED,
            "batchId": batch_id, "sourceBucket": file_info["sourceBucketName"],
            "sourceKey": file_info["tempS3Key"], "sourceVersionId": head_response.get("VersionId") or "",
            "sourceSize": int(head_response.get("ContentLength", 0) or 0),
            "destBucket": file_info["bucketName"], "destKey": file_info["finalS3Key"],
            "metadata": dict(change_metadata or {})})
        return job_id

    def call(self, operation, **fields):
        if operation == loc.OUTPUT_STEP_RECORDED_JOBS:
            return {"jobs": {j: dict(r) for j, r in self.jobs.items()}}
        if operation == loc.OUTPUT_STEP_RECORD_BATCH:
            self.batch = {"context": fields.get("context") or {}, "jobIds": fields.get("jobIds")}
            return {"claimed": False}
        if operation == loc.OUTPUT_STEP_BATCH_OUTCOMES:
            return {"batch": self.batch, "outcomes": {r["relativeKey"]: dict(r) for r in self.jobs.values()}}
        return {}

    def land(self, fail=False):
        """Every queued copy ends: it copies the pinned version into the asset and deletes exactly
        that staged version (or, failing, leaves both)."""
        for row in self.jobs.values():
            if row["status"] != loc.STATUS_QUEUED:
                continue
            if fail:
                row["status"] = loc.STATUS_FAILED
                row["error"] = "copy failed"
                continue
            body = self.s3.get_object(Bucket=row["sourceBucket"], Key=row["sourceKey"],
                                      VersionId=row["sourceVersionId"])["Body"].read()
            self.s3.put_object(Bucket=row["destBucket"], Key=row["destKey"], Body=body,
                               Metadata={VAMS_CHANGE_WORKFLOW_EXECUTION_ID_METADATA_KEY: EXEC})
            self.s3.delete_object(Bucket=row["sourceBucket"], Key=row["sourceKey"],
                                  VersionId=row["sourceVersionId"])
            row["status"] = loc.STATUS_SUCCEEDED


def _run(first_attempt, with_preview=False, with_large=False, old_code_first=False, real_stamp=False,
         run_bucket_versioned=True, fail_upload_of=None, retry_remaining_ms=900_000,
         kill_every_ingestion=False, engine_fails=False, engine_lands_before_retry=False,
         record_override=None):
    """Run attempt 0, which `first_attempt` ends, then the retries until one answers.

    'after_ingest' | 'mid_move' | 'during_record' kill attempt 0 at that point; 'finished' lets it
    complete; 'overlap' keeps attempt 0 running (blocked before its ingestion call) while attempt 1
    starts, and lets it finish during attempt 1's first wait. An attempt refused because another live
    attempt holds outputs is followed by one past the takeover age (the clock is advanced).

    Returns the last answer, the asset versions per key, the outputs the last attempt recorded, the
    large-file queue calls, the staged keys left, the ingestion calls per attempt and the refusals."""
    s3 = boto3.client("s3", region_name=REGION)
    _stage(s3, with_preview=with_preview, with_large=with_large, run_bucket_versioned=run_bucket_versioned)
    ddb, uploads = _tables()
    enforcer = MagicMock(**{"enforce.return_value": True, "enforceAPI.return_value": True})
    attempt = [0]
    queued = []
    ingestion_calls = []
    clock = [time.time()]
    go, in_upload = threading.Event(), threading.Event()
    first = {}
    handed = []
    killed = [0]
    engine = _FakeCopyEngine(s3)

    def invoke_upload(payload):
        context = MagicMock(**{"get_remaining_time_in_millis.return_value": 900_000})
        ingestion_calls.append(threading.current_thread().name)
        # Each output is sent as its path in the asset with a leading '/'; recorded here by its name
        handed.append((attempt[0], sorted(f["relativeKey"].lstrip("/") for f in json.loads(payload["body"])["files"])))
        if attempt[0] == 0 and first_attempt == "before_upload":
            # Killed after it claimed its outputs, before the upload handler wrote anything.
            raise _Killed()
        if first_attempt == "overlap" and threading.current_thread().name == "attempt-0":
            in_upload.set()
            assert go.wait(20)
        if attempt[0] == 0 and first_attempt == "mixed":
            # SMALL[0]'s copy lands and its staged source stays; SMALL[1] is never copied.
            real_delete, real_copy = uf.delete_s3_object, uf.copy_s3_object

            def delete_or_die(bucket, key):
                if key.endswith(SMALL[0]):
                    raise _Killed()
                return real_delete(bucket, key)

            def copy_or_die(source_bucket, source_key, *args, **kwargs):
                if source_key.endswith(SMALL[1]):
                    raise _Killed()
                return real_copy(source_bucket, source_key, *args, **kwargs)

            with patch.object(uf, "delete_s3_object", side_effect=delete_or_die), \
                    patch.object(uf, "copy_s3_object", side_effect=copy_or_die):
                uf.lambda_handler(json.loads(json.dumps(payload)), context)
        if attempt[0] == 0 and first_attempt == "mid_move":
            # The first output's copy lands; the attempt is killed before its staged source is deleted.
            real_delete = uf.delete_s3_object

            def delete_then_die(bucket, key):
                if key.endswith(SMALL[0]):
                    raise _Killed()
                return real_delete(bucket, key)

            with patch.object(uf, "delete_s3_object", side_effect=delete_then_die):
                uf.lambda_handler(json.loads(json.dumps(payload)), context)
        response = uf.lambda_handler(json.loads(json.dumps(payload)), context)
        if (attempt[0] == 0 and first_attempt == "after_ingest") or kill_every_ingestion:
            # The ingestion call outlasts the function timeout: its writes landed, the attempt died.
            raise _Killed()
        return {"Payload": io.BytesIO(json.dumps(response).encode("utf-8"))}

    def queue(file_info, _url):
        queued.append(file_info["relativeKey"])
        return True

    with ExitStack() as stack:
        p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))  # noqa: E731
        p(po, "lookup_existing_asset", return_value={
            "databaseId": DATABASE_ID, "assetId": ASSET_ID, "bucketId": "b1"})
        p(po, "get_default_bucket_details", return_value={
            "bucketId": "b1", "bucketName": ASSET_BUCKET, "baseAssetsPrefix": "assets/"})
        p(po, "_fetch_execution_logs", return_value=("", ""))
        p(po, "_execution_start_date", return_value="")
        p(po, "_refresh_execution_log")
        p(po, "verify_get_path_objects", side_effect=lambda bucket, prefix: _listing(s3, prefix))
        p(po, "asset_upload_table", uploads)
        p(po, "dynamodb", ddb)
        if real_stamp:
            # The real provenance stamp: a copy of each staged object onto itself (a new version).
            p(po, "s3r", boto3.resource("s3", region_name=REGION))
        else:
            p(po, "_stamp_output_objects", return_value=True)
        p(po, "_lambda_file_ingestion", side_effect=invoke_upload)
        # create=True keeps the harness runnable against code without these seams (the base branch).
        sleep = p(po, "_wait_sleep", create=True)
        p(po, "_now_epoch", create=True, side_effect=lambda: clock[0])
        record = (p(po, "record_execution_outputs", record_override) if record_override is not None
                  else p(po, "record_execution_outputs"))
        if first_attempt == "during_record":
            # The attempt saw the ingestion response and is killed while it records.
            def record_or_die(**_kwargs):
                if attempt[0] == 0:
                    raise _Killed()
            record.side_effect = record_or_die
        if first_attempt == "before_hand_off_record":
            # The engine accepted the large output; the attempt is killed before it records the
            # hand-off (its row stays `moving`), as on demo22 (combined smoke D1).
            def mark_or_die(*args, **kwargs):
                if attempt[0] == 0:
                    raise _Killed()
            p(po, "_mark_engine_copying", side_effect=mark_or_die)
        if first_attempt == "before_wait":
            # The attempt handed its large output to the engine and recorded the hand-off, then was
            # killed before it saved the batch (no batch, no saved state).
            real_begin = po._begin_waiting_for_ingestion

            def begin_or_die(*args, **kwargs):
                if attempt[0] == 0:
                    raise _Killed()
                return real_begin(*args, **kwargs)
            p(po, "_begin_waiting_for_ingestion", side_effect=begin_or_die)
        p(po, "s3c", s3)
        p(uf, "request_to_claims", return_value={"tokens": ["SYSTEM_USER"], "roles": ["SYSTEM"]})
        p(uf, "CasbinEnforcer", return_value=enforcer)
        p(uf, "asset_upload_table", uploads)
        p(uf, "get_asset_details", return_value={
            "assetId": ASSET_ID, "databaseId": DATABASE_ID, "bucketId": "b1",
            "assetLocation": {"Key": f"assets/{ASSET_ID}/"}})
        p(uf, "get_default_bucket_details", return_value={
            "bucketId": "b1", "bucketName": ASSET_BUCKET, "baseAssetsPrefix": "assets/",
            "bucketRegion": REGION})
        p(uf, "bucket_region_for_name", return_value=REGION)
        p(uf, "get_database_details", return_value={"databaseId": DATABASE_ID})
        p(uf, "validateS3AssetExtensionsAndContentType",
          side_effect=lambda _bucket, key: not (fail_upload_of and key.endswith(fail_upload_of)))
        p(uf, "determine_asset_type", return_value=".glb")
        p(uf, "send_subscription_email")
        p(uf, "log_file_upload")
        p(uf, "update_asset_attributes")
        p(uf, "s3", s3)
        p(uf, "copy_s3_object_repeat_safe", new=repeat_safe_copy_on(s3))
        # One move at a time, so the mid-move kill lands on the first output deterministically.
        p(uf, "MAX_PARALLEL_S3_WORKERS", 1)
        p(uf, "LARGE_FILE_THRESHOLD_BYTES", THRESHOLD)
        p(uf, "queue_large_file_for_processing", side_effect=queue)
        # Files over THRESHOLD go to the copy engine (#1123): the same threshold on both sides.
        p(po.loc, "LARGE_FILE_THRESHOLD_BYTES", THRESHOLD)
        p(uf, "start_output_copy_job", side_effect=engine.start)
        p(po, "_engine", side_effect=engine.call)
        p(po, "_mark_ingesting", return_value=True)

        refusals = 0
        if old_code_first:
            # The first attempt ran on a deployment without the ledger.
            with patch.object(po, "_write_ingest_ledger", create=True), \
                    patch.object(po, "_settle_ingest_ledger", create=True), \
                    patch.object(po, "_claim_ingest_rows", create=True,
                                 side_effect=lambda _e, d, *_a, **_k: list(d)):
                with pytest.raises(_Killed):
                    po.lambda_handler(_event(with_preview), _ctx(0))
        elif first_attempt == "finished":
            po.lambda_handler(_event(with_preview), _ctx(0))
        elif first_attempt == "overlap":
            def attempt_zero():
                first["answer"] = po.lambda_handler(_event(with_preview), _ctx(0))

            t0 = threading.Thread(target=attempt_zero, name="attempt-0")
            t0.start()
            assert in_upload.wait(20)

            def finish_attempt_zero(_seconds):
                go.set()
                t0.join(20)

            sleep.side_effect = finish_attempt_zero
        else:
            with pytest.raises(_Killed):
                po.lambda_handler(_event(with_preview), _ctx(0))
        attempt[0] = 1
        if engine_lands_before_retry:
            # The copy lands (and deletes its staged source) before the retry lists the outputs.
            engine.land(fail=engine_fails)
        answer = None
        for i in range(1, 4):
            try:
                answer = po.lambda_handler(_event(with_preview, attempt=i), _ctx(i, retry_remaining_ms))
                break
            except _refused():
                refusals += 1
                attempt[0] = i + 1
                clock[0] += getattr(po.eo, "INGEST_TAKEOVER_SECONDS", 0) + 60
            except _Killed:
                # This retry was killed too (only with kill_every_ingestion): the next one follows.
                killed[0] += 1
                attempt[0] = i + 1
        go.set()

        # Before the engine lands its copies: what is still staged, then the copies end and the
        # FinalizeOutputs state records the run (only when the step answered that copies are pending).
        staged_before_landing = sorted(
            v["Key"] for v in s3.list_object_versions(Bucket=RUN_BUCKET).get("Versions", [])
            if v["Key"].startswith((FILES_PREFIX, PREVIEW_PREFIX)))
        ledger_before_landing = eo.read_ingest_ledger(ddb, po.pipeline_execution_output_files_table, EXEC)
        finalized = None
        if answer and answer.get("outputIngestionPending"):
            engine.land(fail=engine_fails)
            finalized = po.lambda_handler(
                {"body": {"mode": po.MODE_FINALIZE, "batchId": EXEC, "workflowExecutionId": EXEC}},
                _ctx(9))
        ledger = eo.read_ingest_ledger(ddb, po.pipeline_execution_output_files_table, EXEC)

        versions = {}
        for v in s3.list_object_versions(Bucket=ASSET_BUCKET).get("Versions", []):
            versions.setdefault(v["Key"], []).append(v["VersionId"])
        recorded = []
        if record.call_args:
            recorded = sorted(
                (f["fileType"], f["relativeFilePath"].lstrip("/"))
                for f in record.call_args.kwargs["output_files"])
        # The staged outputs (the run bucket also holds the step's saved ingestion state).
        outputs = (FILES_PREFIX, PREVIEW_PREFIX)
        staged = sorted(o["Key"] for o in s3.list_objects_v2(Bucket=RUN_BUCKET).get("Contents", [])
                        if o["Key"].startswith(outputs))
        staged_versions = sorted(
            v["Key"] for v in s3.list_object_versions(Bucket=RUN_BUCKET).get("Versions", [])
            if v["Key"].startswith(outputs))
        record_calls = record.call_count
    return {"answer": answer, "versions": versions, "recorded": recorded, "queued": queued,
            "staged": staged, "staged_versions": staged_versions, "ingestion_calls": ingestion_calls,
            "refusals": refusals, "slept": sleep.call_count, "handed": handed,
            "killed_retries": killed[0],
            "starts": engine.starts, "jobs": engine.jobs, "finalized": finalized,
            "staged_before_landing": staged_before_landing, "record_calls": record_calls,
            "ledger_before_landing": ledger_before_landing, "ledger": ledger,
            "first_answer": first.get("answer")}


def _asset_key(name):
    return f"assets/{ASSET_ID}/{name}"


@pytest.mark.unit
@pytest.mark.aws
class TestRetryAfterTimeoutRecordsEveryOutput:
    def test_the_outputs_the_first_attempt_moved_are_recorded(self):
        with mock_aws():
            landed = _run("after_ingest", with_preview=True)
        assert landed["answer"]["statusCode"] == 200
        assert landed["recorded"] == sorted(
            [("file", name) for name in SMALL] + [("preview", PREVIEW)])

    def test_no_output_is_written_twice(self):
        with mock_aws():
            landed = _run("after_ingest", with_preview=True)
        for name in SMALL:
            assert len(landed["versions"][_asset_key(name)]) == 1, landed["versions"]
        assert len(landed["versions"][f"assets/previews/{ASSET_ID}/{PREVIEW}"]) == 1


@pytest.mark.unit
@pytest.mark.aws
class TestRetryAfterAMidMoveTimeout:
    def test_the_output_caught_mid_move_has_one_version(self):
        with mock_aws():
            landed = _run("mid_move")
        for name in SMALL:
            assert len(landed["versions"][_asset_key(name)]) == 1, landed["versions"]

    def test_every_output_is_recorded_and_nothing_is_left_staged(self):
        with mock_aws():
            landed = _run("mid_move")
        assert landed["recorded"] == sorted(("file", name) for name in SMALL)
        assert landed["staged"] == []

    def test_the_killed_holders_rows_are_left_until_the_takeover_age_then_taken_over(self):
        with mock_aws():
            landed = _run("mid_move")
        # Attempt 1 cannot tell the killed holder from a live one: it refuses rather than ingest beside
        # it; the attempt after the takeover age takes the rows over and completes. Both outputs' writes
        # had landed (the copy of the second ran on in the upload's pool), so nothing is re-ingested.
        assert landed["refusals"] == 1
        assert landed["answer"]["statusCode"] == 200
        assert landed["ingestion_calls"].count("MainThread") == 1


@pytest.mark.unit
@pytest.mark.aws
class TestAShortFunctionTimeout:
    """[in-dev D3] With a process-output timeout of 30 s, a retry still finishes the run."""

    def test_a_killed_attempts_finished_upload_is_taken_over_within_the_attempt(self):
        with mock_aws():
            # The real stamp: the killed attempt's staged original stays listed (it died before it
            # removed it), so the retry must deal with that attempt's unsettled row.
            landed = _run("after_ingest", with_preview=True, real_stamp=True, retry_remaining_ms=30_000)
        # Attempt 0's preview upload finished but it died before settling: attempt 1 waits the (scaled)
        # grace, takes the row over and completes, instead of refusing at once with no wait budget.
        assert landed["refusals"] == 0
        assert landed["slept"] > 0
        assert landed["answer"]["statusCode"] == 200
        assert landed["recorded"] == sorted(
            [("file", name) for name in SMALL] + [("preview", PREVIEW)])
        for name in SMALL:
            assert len(landed["versions"][_asset_key(name)]) == 1, landed["versions"]

    def test_every_output_already_ingested_is_recorded_without_waiting(self):
        with mock_aws():
            landed = _run("during_record", with_preview=True, retry_remaining_ms=30_000)
        assert landed["refusals"] == 0 and landed["slept"] == 0
        assert landed["ingestion_calls"] == ["MainThread", "MainThread"]  # attempt 0's two calls only
        assert landed["answer"]["statusCode"] == 200
        assert landed["recorded"] == sorted(
            [("file", name) for name in SMALL] + [("preview", PREVIEW)])


@pytest.mark.unit
@pytest.mark.aws
class TestTakingOverALandedWrite:
    """A taken-over output whose write already landed is recorded from that evidence, not re-ingested
    (owner decision 2026-11-10). Without it, an ingestion call that always outlasts the function
    timeout kills every attempt mid-call and the run never finishes."""

    def test_an_ingestion_that_always_exceeds_the_timeout_still_finishes(self):
        with mock_aws():
            # Every ingestion call is killed after its writes land. Attempt 0 lands the preview (written
            # first) and dies; attempt 1 records the preview from that evidence, hands over the files
            # and dies too; attempt 2 has evidence for everything and finishes without ingesting.
            landed = _run("after_ingest", with_preview=True, real_stamp=True,
                          retry_remaining_ms=30_000, kill_every_ingestion=True)
        assert landed["answer"] is not None and landed["answer"]["statusCode"] == 200
        assert landed["handed"] == [(0, [PREVIEW]), (1, sorted(SMALL))]
        assert landed["killed_retries"] == 1
        assert landed["recorded"] == sorted(
            [("file", name) for name in SMALL] + [("preview", PREVIEW)])
        assert len(landed["versions"][f"assets/previews/{ASSET_ID}/{PREVIEW}"]) == 1
        for name in SMALL:
            assert len(landed["versions"][_asset_key(name)]) == 1, landed["versions"]
        assert landed["staged_versions"] == []

    def test_a_holder_killed_before_its_upload_wrote_is_re_ingested_once(self):
        with mock_aws():
            landed = _run("before_upload", retry_remaining_ms=30_000)
        assert landed["answer"]["statusCode"] == 200
        assert landed["handed"] == [(0, sorted(SMALL)), (1, sorted(SMALL))]
        assert landed["recorded"] == sorted(("file", name) for name in SMALL)
        for name in SMALL:
            assert len(landed["versions"][_asset_key(name)]) == 1, landed["versions"]
        assert landed["staged"] == []

    def test_mixed_only_the_output_without_a_landed_write_is_re_ingested(self):
        with mock_aws():
            # Attempt 0 copied SMALL[0] and was killed before deleting its staged source; SMALL[1]
            # was never written. The retry past the takeover age hands over only SMALL[1].
            landed = _run("mixed")
        assert landed["answer"]["statusCode"] == 200
        assert landed["handed"][-1] == (landed["handed"][-1][0], [SMALL[1]])
        assert landed["recorded"] == sorted(("file", name) for name in SMALL)
        for name in SMALL:
            assert len(landed["versions"][_asset_key(name)]) == 1, landed["versions"]
        assert landed["staged"] == []


@pytest.mark.unit
@pytest.mark.aws
class TestAMoveLeavesNothingStaged:
    """#1291: the provenance stamp writes a new version of each staged object and the move deleted only
    the current one, so the version the pipeline wrote became current again and every output stayed
    staged. These run the real stamp."""

    def test_no_version_remains_under_a_moved_staged_key(self):
        with mock_aws():
            landed = _run("finished", with_preview=True, real_stamp=True)
        assert landed["answer"]["statusCode"] == 200
        assert landed["staged"] == []
        assert landed["staged_versions"] == []
        for name in SMALL:
            assert len(landed["versions"][_asset_key(name)]) == 1, landed["versions"]

    def test_an_unversioned_run_bucket_is_left_empty_too(self):
        with mock_aws():
            landed = _run("finished", real_stamp=True, run_bucket_versioned=False)
        assert landed["answer"]["statusCode"] == 200
        assert landed["staged"] == []
        for name in SMALL:
            assert len(landed["versions"][_asset_key(name)]) == 1, landed["versions"]

    def test_an_output_whose_move_failed_keeps_its_staged_source(self):
        with mock_aws():
            landed = _run("finished", real_stamp=True, fail_upload_of=SMALL[1])
        assert landed["staged"] == [FILES_PREFIX + SMALL[1]]
        assert _asset_key(SMALL[1]) not in landed["versions"]
        assert FILES_PREFIX + SMALL[0] not in landed["staged_versions"]

    def test_a_large_output_keeps_the_version_its_copy_job_pinned_until_it_lands(self):
        with mock_aws():
            landed = _run("finished", real_stamp=True, with_large=True)
        # The small outputs' staged versions are gone; the large one's stays, unstamped, as long as its
        # copy job reads it (#1291 must not remove it), and the job deletes it once it lands.
        assert landed["staged_before_landing"] == [FILES_PREFIX + LARGE]
        (job,) = landed["jobs"].values()
        assert job["sourceVersionId"]
        assert landed["staged_versions"] == []
        assert len(landed["versions"][_asset_key(LARGE)]) == 1


@pytest.mark.unit
@pytest.mark.aws
class TestOverlappingAttempts:
    """[in-dev D1] A timed-out attempt keeps running beside its retry (a state timeout does not stop the
    Lambda). Each output is ingested once, by one attempt, and the retry completes."""

    def test_attempt_two_waits_for_attempt_one_and_ingests_nothing_itself(self):
        with mock_aws():
            landed = _run("overlap", with_preview=True)
        assert landed["ingestion_calls"] == ["attempt-0", "attempt-0"]  # files + preview, attempt 0 only
        assert landed["first_answer"]["statusCode"] == 200
        assert landed["answer"]["statusCode"] == 200
        assert landed["refusals"] == 0
        for name in SMALL:
            assert len(landed["versions"][_asset_key(name)]) == 1, landed["versions"]
        assert landed["recorded"] == sorted(
            [("file", name) for name in SMALL] + [("preview", PREVIEW)])

    def test_the_run_end_is_recorded_once(self):
        # Audit contract: one workflowExecutionEnd per run. The attempt whose status write was refused
        # (an overlapping attempt already recorded the end) records its ingest only.
        writes = iter([True, False, False, False])
        with mock_aws(), patch.object(po, "log_action") as log_action:
            real_record = po.record_execution_outputs
            try:
                po.record_execution_outputs = MagicMock(side_effect=lambda **_k: next(writes))
                landed = _run("finished", record_override=po.record_execution_outputs)
            finally:
                po.record_execution_outputs = real_record
        actions = [c.args[1] for c in log_action.call_args_list]
        assert landed["answer"]["statusCode"] == 200
        assert actions.count("workflowExecutionEnd") == 1
        assert actions.count("workflowOutputIngest") == 2

    def test_attempt_two_after_attempt_one_finished_ingests_nothing_new(self):
        with mock_aws():
            landed = _run("finished", with_preview=True)
        assert landed["ingestion_calls"] == ["MainThread", "MainThread"]  # attempt 0's two calls only
        assert landed["answer"]["statusCode"] == 200
        assert landed["recorded"] == sorted(
            [("file", name) for name in SMALL] + [("preview", PREVIEW)])


@pytest.mark.unit
@pytest.mark.aws
class TestUpgradeOverAnAttemptThatRanOnOldCode:
    def test_the_retry_completes_without_a_second_version(self):
        with mock_aws():
            landed = _run("mid_move", old_code_first=True)
        assert landed["answer"]["statusCode"] == 200
        for name in SMALL:
            assert len(landed["versions"][_asset_key(name)]) == 1, landed["versions"]
        # With no ledger the retry can only record what it still found staged: the output caught
        # mid-move. The output the old attempt fully moved is in the asset but not in the record.
        assert ("file", SMALL[0]) in landed["recorded"]
        assert landed["staged"] == []


def _recorded_once(landed, names):
    assert landed["recorded"] == sorted(("file", n) for n in names)
    for name in names:
        assert len(landed["versions"][_asset_key(name)]) == 1, landed["versions"]


@pytest.mark.unit
@pytest.mark.aws
class TestALargeOutputAcrossARetry:
    """Outputs handed to the copy engine across a retried or taken-over output step (#1271 + #1123):
    one job, one asset version, one record, and the ledger row settled only once the copy landed."""

    def test_a_retry_after_the_hand_off_starts_no_second_job(self):
        with mock_aws():
            landed = _run("before_wait", with_large=True)
        # Attempt 0 handed the large file to its job and recorded `engine-copying`; the retry finds that
        # row, hands nothing over again and waits on the same job.
        assert landed["starts"] == [loc.job_id_for(EXEC, LARGE)]
        assert landed["answer"]["outputIngestionPending"] is True
        _recorded_once(landed, (*SMALL, LARGE))
        assert landed["record_calls"] == 1

    @pytest.mark.parametrize("engine_lands_before_retry", [True, False])
    def test_a_hand_off_the_killed_attempt_never_recorded_settles_at_finalize(self, engine_lands_before_retry):
        # Combined smoke D1: the attempt died after the engine accepted the large output and before it
        # wrote `engine-copying`. Whether the copy landed before the retry (the retry no longer lists the
        # file) or after, the row ends `ingested` once FinalizeOutputs sees the job succeeded.
        with mock_aws():
            landed = _run("before_hand_off_record", with_large=True,
                          engine_lands_before_retry=engine_lands_before_retry)
        assert set(landed["starts"]) == {loc.job_id_for(EXEC, LARGE)}
        _recorded_once(landed, (*SMALL, LARGE))
        row = landed["ledger"][f"file:{LARGE}"]
        assert row["ingestState"] == eo.INGEST_STATE_INGESTED
        assert row["engineJobId"] == loc.job_id_for(EXEC, LARGE)

    def test_a_failed_copy_the_killed_attempt_never_recorded_is_released(self):
        with mock_aws():
            landed = _run("before_hand_off_record", with_large=True, engine_fails=True,
                          engine_lands_before_retry=True)
        assert f"file:{LARGE}" not in landed["ledger"]
        assert landed["finalized"]["failedFiles"] == 1

    def test_a_takeover_while_the_copy_runs_starts_no_second_job(self):
        with mock_aws():
            landed = _run("after_ingest", with_large=True)
        # Attempt 0 was killed before it saw the hand-off, so its rows stayed `moving`; the retry takes
        # them over and hands the file to the job again, which is the same job (no second copy).
        job_id = loc.job_id_for(EXEC, LARGE)
        assert set(landed["starts"]) == {job_id}
        assert len(landed["jobs"]) == 1
        _recorded_once(landed, (*SMALL, LARGE))

    def test_the_ledger_row_is_engine_copying_until_the_copy_lands(self):
        with mock_aws():
            landed = _run("finished", with_large=True)
        key = f"file:{LARGE}"
        before = landed["ledger_before_landing"][key]
        assert before["ingestState"] == eo.INGEST_STATE_ENGINE_COPYING
        assert before["engineJobId"] == loc.job_id_for(EXEC, LARGE)
        assert landed["ledger"][key]["ingestState"] == eo.INGEST_STATE_INGESTED
        for name in SMALL:
            assert landed["ledger_before_landing"][f"file:{name}"]["ingestState"] == eo.INGEST_STATE_INGESTED

    def test_mixed_small_and_large_outputs_are_all_recorded_once(self):
        with mock_aws():
            landed = _run("mid_move", with_large=True)
        assert set(landed["starts"]) == {loc.job_id_for(EXEC, LARGE)}
        _recorded_once(landed, (*SMALL, LARGE))
        assert landed["staged"] == []

    def test_a_failed_copy_fails_the_run_and_releases_its_row(self):
        with mock_aws():
            landed = _run("finished", with_large=True, engine_fails=True)
        assert f"file:{LARGE}" not in landed["ledger"]
        # The failed file is left out of the record and named in the run's error.
        assert landed["recorded"] == sorted(("file", n) for n in SMALL)
        assert landed["finalized"]["failedFiles"] == 1
        # Its staged source is kept (the engine deletes it only after landing).
        assert landed["staged"] == [FILES_PREFIX + LARGE]


@pytest.mark.unit
class TestAwaitEarlierIngestion:
    NOW = 1_800_000_000

    def _ledger(self, upload_id="y1", state="moving", holder="attempt-0", age=10):
        return {"file:a.glb": {"ingestState": state, "uploadId": upload_id, "ingestHolder": holder,
                               "movingSince": self.NOW - age}}

    def _wait(self, ledgers, upload_items, remaining_ms=900_000):
        table = MagicMock(**{"get_item.side_effect": upload_items})
        context = MagicMock(**{"get_remaining_time_in_millis.return_value": remaining_ms})
        with patch.object(po, "asset_upload_table", table), \
                patch.object(po, "_wait_sleep") as sleep, \
                patch.object(po, "_now_epoch", return_value=self.NOW), \
                patch.object(po, "_read_ingest_ledger", side_effect=ledgers):
            ledger, takeover = po._await_earlier_ingestion(EXEC, ASSET_ID, context, "attempt-1")
        return ledger, takeover, sleep

    def test_waits_while_a_live_attempt_moves_and_returns_the_ledger_read_after(self):
        settled = self._ledger(state="ingested")
        ledger, takeover, sleep = self._wait(
            [self._ledger(), self._ledger(), settled],
            [{"Item": {"status": "processing"}}, {"Item": {"status": "processing"}}])
        assert ledger == settled and takeover == {}
        assert sleep.call_count == 2

    def test_the_wait_is_bounded_by_the_remaining_time(self):
        # 900 s left keeps the full 300 s margin; the wait is capped at EARLIER_INGESTION_WAIT_MAX_SECONDS.
        ledger, takeover, sleep = self._wait(
            [self._ledger()] * 200, [{"Item": {"status": "processing"}}] * 200, remaining_ms=900_000)
        assert ledger["file:a.glb"]["ingestState"] == "moving" and takeover == {}
        assert sleep.call_count == po.EARLIER_INGESTION_WAIT_MAX_SECONDS // po.EARLIER_INGESTION_POLL_SECONDS

    def test_a_short_function_timeout_still_leaves_time_to_wait(self):
        """[in-dev D3] The margin is at most a third of the attempt's time: with 60 s left the attempt
        keeps 20 s and waits 40 s, rather than having no wait budget at all after a fixed 300 s."""
        ledger, takeover, sleep = self._wait(
            [self._ledger()] * 20, [{"Item": {"status": "processing"}}] * 20, remaining_ms=60_000)
        assert takeover == {}
        assert sleep.call_count == 40 // po.EARLIER_INGESTION_POLL_SECONDS

    def test_a_holder_whose_upload_finished_but_never_settled_is_taken_over_after_the_grace(self):
        ledger, takeover, sleep = self._wait([self._ledger()] * 30, [{}] * 30)
        assert takeover == {"file:a.glb": ("attempt-0", self.NOW - 10)}
        assert sleep.call_count == po.EARLIER_INGESTION_SETTLE_GRACE_SECONDS // po.EARLIER_INGESTION_POLL_SECONDS

    def test_no_wait_for_an_ingested_a_stale_or_an_own_row(self):
        for row in (self._ledger(state="ingested"), self._ledger(age=po.eo.INGEST_TAKEOVER_SECONDS + 1),
                    self._ledger(holder="attempt-1")):
            _ledger, takeover, sleep = self._wait([row], [])
            sleep.assert_not_called()
            assert takeover == {}


@pytest.mark.unit
@pytest.mark.aws
class TestTheMovingClaimIsConditional:
    """claim_ingest_rows never takes a row already `ingested` or a live attempt's `moving` row."""

    def _claim(self, existing, holder="attempt-1", takeover=None):
        with mock_aws():
            ddb, _uploads = _tables()
            table = ddb.Table(po.pipeline_execution_output_files_table)
            d = {"fileType": "file", "relativeFilePath": "a.glb", "s3Key": FILES_PREFIX + "a.glb"}
            if existing:
                table.put_item(Item={"pipelineExecutionId": f"ingest#{EXEC}",
                                     "fileType:relativeFilePath": "file:a.glb", **existing})
            claimed = eo.claim_ingest_rows(ddb, po.pipeline_execution_output_files_table, EXEC, [d],
                                           holder, "y2", 1_800_000_000, takeover=takeover)
            row = table.get_item(Key={"pipelineExecutionId": f"ingest#{EXEC}",
                                      "fileType:relativeFilePath": "file:a.glb"})["Item"]
        return claimed, row

    def test_a_free_output_is_claimed(self):
        claimed, row = self._claim(None)
        assert len(claimed) == 1 and row["ingestHolder"] == "attempt-1" and row["ingestState"] == "moving"

    def test_an_ingested_row_is_never_overwritten(self):
        claimed, row = self._claim({"ingestState": "ingested"})
        assert claimed == [] and row["ingestState"] == "ingested"

    def test_a_live_attempts_moving_row_is_not_taken(self):
        claimed, row = self._claim({"ingestState": "moving", "ingestHolder": "attempt-0",
                                    "movingSince": 1_800_000_000 - 30})
        assert claimed == [] and row["ingestHolder"] == "attempt-0"

    def test_a_stale_moving_row_past_the_takeover_age_is_taken_over(self):
        claimed, row = self._claim({"ingestState": "moving", "ingestHolder": "attempt-0",
                                    "movingSince": 1_800_000_000 - eo.INGEST_TAKEOVER_SECONDS - 1})
        assert len(claimed) == 1 and row["ingestHolder"] == "attempt-1"

    def test_a_row_from_before_holders_were_recorded_is_taken_over(self):
        claimed, row = self._claim({"ingestState": "moving", "uploadId": "y1"})
        assert len(claimed) == 1 and row["ingestHolder"] == "attempt-1"

    def test_an_abandoned_row_is_taken_over_only_if_unchanged(self):
        live = {"ingestState": "moving", "ingestHolder": "attempt-0", "movingSince": 1_800_000_000 - 30}
        claimed, _row = self._claim(live, takeover={"file:a.glb": ("attempt-0", 1_800_000_000 - 30)})
        assert len(claimed) == 1
        claimed, row = self._claim(live, takeover={"file:a.glb": ("attempt-0", 1_800_000_000 - 99)})
        assert claimed == [] and row["ingestHolder"] == "attempt-0"


@pytest.mark.unit
class TestTheStoodAsideAttemptIsRetried:
    def test_the_raised_error_name_is_in_the_process_output_retrier(self):
        from backend.backend.common.workflows import workflowAslBuilder as asl
        assert po.EarlierIngestionInProgress.__name__ in asl.PROCESS_OUTPUT_RETRY_ERRORS


@pytest.mark.unit
class TestMergeEarlierOutputs:
    def _row(self, name, state, file_type="file"):
        return {"fileType": file_type, "relativeFilePath": name, "s3Bucket": RUN_BUCKET,
                "s3Key": FILES_PREFIX + name, "fileSize": Decimal(5), "contentType": "",
                "s3VersionId": "v1", "ingestState": state}

    def test_a_moving_row_counts_only_when_its_listing_was_read_and_it_is_gone(self):
        ledger = {"file:a.glb": self._row("a.glb", eo.INGEST_STATE_MOVING),
                  "file:b.glb": self._row("b.glb", eo.INGEST_STATE_MOVING)}
        collected = []
        po._merge_earlier_outputs(collected, ledger, {"file": {FILES_PREFIX + "b.glb"}})
        assert [d["relativeFilePath"] for d in collected] == ["a.glb"]
        assert collected[0]["fileSize"] == 5

        unread = []
        po._merge_earlier_outputs(unread, ledger, {})
        assert unread == []

    def test_an_output_this_attempt_recorded_is_not_added_twice(self):
        ledger = {"file:a.glb": self._row("a.glb", eo.INGEST_STATE_INGESTED)}
        collected = [{"fileType": "file", "relativeFilePath": "a.glb", "s3Key": "k"}]
        po._merge_earlier_outputs(collected, ledger, {"file": set()})
        assert len(collected) == 1


@pytest.mark.unit
class TestTheLedgerIsBestEffort:
    def test_a_failed_ledger_read_or_write_does_not_fail_the_step(self):
        with patch.object(po.eo, "read_ingest_ledger", side_effect=RuntimeError("ddb down")):
            assert po._read_ingest_ledger(EXEC) == {}
        with patch.object(po.eo, "write_ingest_ledger", side_effect=RuntimeError("ddb down")):
            po._write_ingest_ledger(EXEC, [{"fileType": "file", "relativeFilePath": "a"}], "moving")
        with patch.object(po.eo, "drop_ingest_ledger_rows", side_effect=RuntimeError("ddb down")):
            po._drop_ingest_ledger_rows(EXEC, [{"fileType": "file", "relativeFilePath": "a"}])


@pytest.mark.unit
@pytest.mark.aws
class TestDestinationWrittenByExecution:
    def test_only_this_executions_version_counts(self):
        with mock_aws():
            s3 = boto3.client("s3", region_name=REGION)
            s3.create_bucket(Bucket=ASSET_BUCKET)
            s3.put_object(Bucket=ASSET_BUCKET, Key="mine", Body=b"x",
                          Metadata={VAMS_CHANGE_WORKFLOW_EXECUTION_ID_METADATA_KEY: EXEC})
            s3.put_object(Bucket=ASSET_BUCKET, Key="other", Body=b"x",
                          Metadata={VAMS_CHANGE_WORKFLOW_EXECUTION_ID_METADATA_KEY: "f" * 32})
            s3.put_object(Bucket=ASSET_BUCKET, Key="user", Body=b"x")
            with patch.object(uf, "s3", s3):
                assert uf.destination_written_by_execution(ASSET_BUCKET, "mine", EXEC) is True
                assert uf.destination_written_by_execution(ASSET_BUCKET, "other", EXEC) is False
                assert uf.destination_written_by_execution(ASSET_BUCKET, "user", EXEC) is False
                assert uf.destination_written_by_execution(ASSET_BUCKET, "missing", EXEC) is False
                assert uf.destination_written_by_execution(ASSET_BUCKET, "mine", None) is False
