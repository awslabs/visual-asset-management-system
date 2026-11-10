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
- a large output the first attempt queued for its asynchronous copy is not queued a second time.

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


class _Killed(BaseException):
    """The Lambda runtime ending an attempt at its timeout."""


def _event(with_preview=False):
    body = {
        "outputAssetId": ASSET_ID, "outputDatabaseId": DATABASE_ID, "workflowExecutionId": EXEC,
        "endStatePipelineExecutionId": "P1", "workflowDatabaseId": "GLOBAL", "workflowId": "wf1",
        "workflowExecutionS3InputOutputBucket": RUN_BUCKET,
        "executingUserName": "SYSTEM_USER", "executingRequestContext": {"http": {}},
        "filesPathKey": FILES_PREFIX,
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


def _stage(s3, with_preview=False, with_large=False):
    for bucket in (RUN_BUCKET, ASSET_BUCKET):
        s3.create_bucket(Bucket=bucket)
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


def _ctx(i):
    """A Lambda context per attempt: its own request id, a full 15 minutes left."""
    return MagicMock(aws_request_id=f"attempt-{i}", **{"get_remaining_time_in_millis.return_value": 900_000})


def _refused():
    """The error an attempt raises instead of ingesting outputs another live attempt holds (absent on
    code without the ledger)."""
    return getattr(po, "EarlierIngestionInProgress", _Never)


class _Never(Exception):
    pass


def _run(first_attempt, with_preview=False, with_large=False, old_code_first=False):
    """Run attempt 0, which `first_attempt` ends, then the retries until one answers.

    'after_ingest' | 'mid_move' | 'during_record' kill attempt 0 at that point; 'finished' lets it
    complete; 'overlap' keeps attempt 0 running (blocked before its ingestion call) while attempt 1
    starts, and lets it finish during attempt 1's first wait. An attempt refused because another live
    attempt holds outputs is followed by one past the takeover age (the clock is advanced).

    Returns the last answer, the asset versions per key, the outputs the last attempt recorded, the
    large-file queue calls, the staged keys left, the ingestion calls per attempt and the refusals."""
    s3 = boto3.client("s3", region_name=REGION)
    _stage(s3, with_preview=with_preview, with_large=with_large)
    ddb, uploads = _tables()
    enforcer = MagicMock(**{"enforce.return_value": True, "enforceAPI.return_value": True})
    attempt = [0]
    queued = []
    ingestion_calls = []
    clock = [time.time()]
    go, in_upload = threading.Event(), threading.Event()
    first = {}

    def invoke_upload(payload):
        context = MagicMock(**{"get_remaining_time_in_millis.return_value": 900_000})
        ingestion_calls.append(threading.current_thread().name)
        if first_attempt == "overlap" and threading.current_thread().name == "attempt-0":
            in_upload.set()
            assert go.wait(20)
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
        if attempt[0] == 0 and first_attempt == "after_ingest":
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
        p(po, "_stamp_output_objects", return_value=True)
        p(po, "_lambda_file_ingestion", side_effect=invoke_upload)
        # create=True keeps the harness runnable against code without these seams (the base branch).
        sleep = p(po, "_wait_sleep", create=True)
        p(po, "_now_epoch", create=True, side_effect=lambda: clock[0])
        record = p(po, "record_execution_outputs")
        if first_attempt == "during_record":
            # The attempt saw the ingestion response and is killed while it records.
            def record_or_die(**_kwargs):
                if attempt[0] == 0:
                    raise _Killed()
            record.side_effect = record_or_die
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
        p(uf, "validateS3AssetExtensionsAndContentType", return_value=True)
        p(uf, "determine_asset_type", return_value=".glb")
        p(uf, "send_subscription_email")
        p(uf, "log_file_upload")
        p(uf, "update_asset_attributes")
        p(uf, "s3", s3)
        p(uf.s3_resource.meta, "client", s3)
        # One move at a time, so the mid-move kill lands on the first output deterministically.
        p(uf, "MAX_PARALLEL_S3_WORKERS", 1)
        p(uf, "LARGE_FILE_THRESHOLD_BYTES", THRESHOLD)
        p(uf, "queue_large_file_for_processing", side_effect=queue)

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
        answer = None
        for i in range(1, 4):
            try:
                answer = po.lambda_handler(_event(with_preview), _ctx(i))
                break
            except _refused():
                refusals += 1
                attempt[0] = i + 1
                clock[0] += getattr(po.eo, "INGEST_TAKEOVER_SECONDS", 0) + 60
        go.set()

        versions = {}
        for v in s3.list_object_versions(Bucket=ASSET_BUCKET).get("Versions", []):
            versions.setdefault(v["Key"], []).append(v["VersionId"])
        recorded = []
        if record.call_args:
            recorded = sorted(
                (f["fileType"], f["relativeFilePath"].lstrip("/"))
                for f in record.call_args.kwargs["output_files"])
        staged = sorted(o["Key"] for o in s3.list_objects_v2(Bucket=RUN_BUCKET).get("Contents", []))
    return {"answer": answer, "versions": versions, "recorded": recorded, "queued": queued,
            "staged": staged, "ingestion_calls": ingestion_calls, "refusals": refusals,
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
        # it; the attempt after the takeover age takes the rows over and completes.
        assert landed["refusals"] == 1
        assert landed["answer"]["statusCode"] == 200
        assert landed["ingestion_calls"].count("MainThread") == 2


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


@pytest.mark.unit
@pytest.mark.aws
class TestALargeOutputAcrossARetry:
    def test_after_the_ingestion_answered_the_retry_does_not_queue_it_again(self):
        with mock_aws():
            landed = _run("during_record", with_large=True)
        # Its staged source outlives the ingest until the asynchronous copy runs; the ledger says it
        # was ingested, so the retry neither queues it again nor drops it from the record.
        assert landed["queued"] == [LARGE]
        assert landed["recorded"] == sorted(("file", n) for n in (*SMALL, LARGE))

    def test_before_the_ingestion_answered_the_retry_records_it(self):
        with mock_aws():
            landed = _run("after_ingest", with_large=True)
        # The killed attempt never saw that the file was queued, so the retry hands it over again;
        # the asynchronous copy writes it once (TestTheAsyncCopyIsWrittenOnce).
        assert landed["queued"] == [LARGE, LARGE]
        assert landed["recorded"] == sorted(("file", n) for n in (*SMALL, LARGE))


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
        # 320 s left keeps 300 s for this attempt's own work: at most 20 s of waiting.
        ledger, takeover, sleep = self._wait(
            [self._ledger()] * 10, [{"Item": {"status": "processing"}}] * 10, remaining_ms=320_000)
        assert ledger["file:a.glb"]["ingestState"] == "moving" and takeover == {}
        assert sleep.call_count == 20 // po.EARLIER_INGESTION_POLL_SECONDS

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


@pytest.mark.unit
@pytest.mark.aws
class TestTheAsyncCopyIsWrittenOnce:
    """A large output queued twice (the retry of an attempt killed before it saw the ingestion
    response) is copied once: the second message finds this run's version in place."""

    def test_a_second_message_for_a_written_output_writes_no_version(self):
        from backend.backend.handlers.assets import sqsUploadFileLarge as large
        file_info = {
            "bucketName": ASSET_BUCKET, "sourceBucketName": RUN_BUCKET,
            "tempS3Key": FILES_PREFIX + LARGE, "finalS3Key": _asset_key(LARGE),
            "relativeKey": LARGE, "uploadType": "assetFile", "databaseId": DATABASE_ID,
            "assetId": ASSET_ID, "changeUserId": "SYSTEM_USER", "workflowId": "wf1",
            "workflowExecutionId": EXEC,
        }
        with mock_aws():
            s3 = boto3.client("s3", region_name=REGION)
            for bucket in (RUN_BUCKET, ASSET_BUCKET):
                s3.create_bucket(Bucket=bucket)
                s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
            s3.put_object(Bucket=ASSET_BUCKET, Key=_asset_key(LARGE), Body=b"x",
                          Metadata={VAMS_CHANGE_WORKFLOW_EXECUTION_ID_METADATA_KEY: EXEC})
            s3.put_object(Bucket=RUN_BUCKET, Key=FILES_PREFIX + LARGE, Body=b"x")
            ddb, _uploads = _tables()
            with patch.object(large, "s3", s3), \
                    patch.object(large, "dynamodb", ddb), \
                    patch.object(large, "validateS3AssetExtensionsAndContentType", return_value=True), \
                    patch.object(large, "copy_s3_object") as copy, \
                    patch.object(large, "update_asset_after_file_processing") as update:
                assert large.validate_and_move_large_file(file_info, {}) is True
            copy.assert_not_called()
            update.assert_not_called()
            assert len(s3.list_object_versions(Bucket=ASSET_BUCKET)["Versions"]) == 1
            assert "Contents" not in s3.list_objects_v2(Bucket=RUN_BUCKET)
