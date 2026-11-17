# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""[in-dev D2] Overlapping process-output attempts finish the run SUCCEEDED (#1271).

Live (demo24, TimeoutSeconds=35, 604 outputs): attempt 1 settled its rows `ingested` and then spent
about 14 s deleting the staged versions; attempt 2 listed the staging prefix meanwhile, a HEAD on a key
attempt 1 had just deleted answered 404, the whole listing failed, attempt 2 recorded the run FAILED
("Listing the pipeline file output failed."), and attempt 1's SUCCEEDED write was then skipped by the
terminal guard: Step Functions SUCCEEDED, the VAMS record FAILED. Three changes, each pinned here:

- a listed key gone by the time it is HEADed is skipped as already moved, not a failed listing;
- the staged versions are removed BEFORE the rows are settled `ingested`, so an attempt waiting on
  those rows never lists the prefix while it is being emptied;
- a SUCCEEDED the end-state step writes replaces a FAILED the end-state step wrote (marked
  `executionFailedBy`), never a FAILED or ABORTED from the error handler or the abort path, and never
  the other way round.
"""

from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from backend.tests.handlers.workflows import test_processOutput_retry_records_all_outputs as harness

po = harness.po
eo = harness.eo
REGION = harness.REGION
EXEC = harness.EXEC
WF_DB, WF_ID, PEXEC = "GLOBAL", "wf1", "P1"
MAIN_KEY = {"workflowExecutionId": EXEC, "workflowDatabaseId:workflowId": WF_DB + ":" + WF_ID}
PIPE_KEY = {"pipelineExecutionId": PEXEC, "workflowExecutionId": EXEC}


@pytest.mark.unit
@pytest.mark.aws
class TestAListedKeyMovedBeforeItsHead:
    def test_is_skipped_and_the_listing_succeeds(self):
        with mock_aws():
            s3 = boto3.client("s3", region_name=REGION)
            s3.create_bucket(Bucket="run-1271-d2")
            for name in ("a.glb", "b.glb"):
                s3.put_object(Bucket="run-1271-d2", Key=f"files/{name}", Body=b"x",
                              ContentType="model/gltf-binary")
            stale = [dict(o) for o in s3.list_objects_v2(Bucket="run-1271-d2")["Contents"]]
            # Another attempt moves a.glb between the listing and the HEAD.
            s3.delete_object(Bucket="run-1271-d2", Key="files/a.glb")
            with patch.object(po, "s3c", s3), \
                    patch.object(po, "list_all_objects", return_value=stale):
                found = po.verify_get_path_objects("run-1271-d2", "files/")
        assert [o["Key"] for o in found["Contents"]] == ["files/b.glb"]

    def test_another_head_error_still_fails_the_listing(self):
        from botocore.exceptions import ClientError
        s3 = MagicMock(**{"head_object.side_effect": ClientError(
            {"Error": {"Code": "AccessDenied"}}, "HeadObject")})
        with patch.object(po, "s3c", s3), \
                patch.object(po, "list_all_objects", return_value=[{"Key": "files/a.glb"}]):
            with pytest.raises(ClientError):
                po.verify_get_path_objects("run", "files/")


@pytest.mark.unit
class TestStagedVersionsGoBeforeTheRowsSettle:
    def test_the_order_is_remove_then_settle(self):
        order = []
        with mock_aws():
            real_write = po._write_ingest_ledger

            def write(execution_id, descriptors, state, upload_id=""):
                if state == eo.INGEST_STATE_INGESTED:
                    order.append("settle")
                return real_write(execution_id, descriptors, state, upload_id=upload_id)

            with patch.object(po, "_remove_listed_staged_versions",
                              side_effect=lambda *a, **k: order.append("remove")), \
                    patch.object(po, "_write_ingest_ledger", side_effect=write):
                harness._run("finished", with_preview=True)
        # One pair per write-back (files, preview), each removing before settling.
        assert order == ["remove", "settle", "remove", "settle"]


def _status_tables(ddb):
    main = ddb.create_table(
        TableName=po.workflow_execution_database_v2,
        KeySchema=[{"AttributeName": "workflowExecutionId", "KeyType": "HASH"},
                   {"AttributeName": "workflowDatabaseId:workflowId", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": "workflowExecutionId", "AttributeType": "S"},
                              {"AttributeName": "workflowDatabaseId:workflowId", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST")
    pipe = ddb.create_table(
        TableName=po.pipeline_executions_table,
        KeySchema=[{"AttributeName": "pipelineExecutionId", "KeyType": "HASH"},
                   {"AttributeName": "workflowExecutionId", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": "pipelineExecutionId", "AttributeType": "S"},
                              {"AttributeName": "workflowExecutionId", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST")
    ddb.create_table(
        TableName=po.pipeline_execution_logs_table,
        KeySchema=[{"AttributeName": "pipelineExecutionId", "KeyType": "HASH"},
                   {"AttributeName": "logType", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": "pipelineExecutionId", "AttributeType": "S"},
                              {"AttributeName": "logType", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST")
    main.put_item(Item={**MAIN_KEY, "executionStatus": "RUNNING"})
    pipe.put_item(Item={**PIPE_KEY, "executionStatus": "RUNNING"})
    return main, pipe


def _end_state_write(ddb, status, error=""):
    return po._write_execution_outputs(
        ddb, EXEC, PEXEC, WF_DB, WF_ID, "bucket", [], [], [], "log", "", "", "", status, error)


@pytest.mark.unit
@pytest.mark.aws
class TestOverlappingStatusWrites:
    def test_a_later_success_replaces_an_end_state_failure(self):
        with mock_aws():
            ddb = boto3.resource("dynamodb", region_name=REGION)
            main, pipe = _status_tables(ddb)
            _end_state_write(ddb, "FAILED", "Listing the pipeline file output failed.")
            _end_state_write(ddb, "SUCCEEDED")
            row = main.get_item(Key=MAIN_KEY)["Item"]
            prow = pipe.get_item(Key=PIPE_KEY)["Item"]
        assert row["executionStatus"] == "SUCCEEDED"
        assert "executionError" not in row and "executionFailedBy" not in row
        assert prow["executionStatus"] == "SUCCEEDED" and "executionFailedBy" not in prow

    def test_the_write_reports_whether_it_recorded_the_end(self):
        # The handler records workflowExecutionEnd only when its own status write took effect.
        with mock_aws():
            ddb = boto3.resource("dynamodb", region_name=REGION)
            _status_tables(ddb)
            assert _end_state_write(ddb, "FAILED", "Listing the pipeline file output failed.") is True
            assert _end_state_write(ddb, "SUCCEEDED") is True
            assert _end_state_write(ddb, "SUCCEEDED") is False
            assert _end_state_write(ddb, "FAILED", "x") is False

    def test_a_later_failure_never_replaces_a_success(self):
        with mock_aws():
            ddb = boto3.resource("dynamodb", region_name=REGION)
            main, pipe = _status_tables(ddb)
            _end_state_write(ddb, "SUCCEEDED")
            _end_state_write(ddb, "FAILED", "Listing the pipeline file output failed.")
            row = main.get_item(Key=MAIN_KEY)["Item"]
        assert row["executionStatus"] == "SUCCEEDED" and "executionError" not in row

    @pytest.mark.parametrize("status", ["FAILED", "ABORTED"])
    def test_an_error_handler_or_abort_status_is_never_replaced(self, status):
        with mock_aws():
            ddb = boto3.resource("dynamodb", region_name=REGION)
            main, pipe = _status_tables(ddb)
            eo.finalize_main_row(ddb, po.workflow_execution_database_v2, EXEC, WF_DB, WF_ID, status,
                                 "2026-11-10T00:00:00Z", execution_error="stopped")
            eo.set_pipeline_status(ddb, po.pipeline_executions_table, PEXEC, EXEC, status)
            _end_state_write(ddb, "SUCCEEDED")
            row = main.get_item(Key=MAIN_KEY)["Item"]
            prow = pipe.get_item(Key=PIPE_KEY)["Item"]
        assert row["executionStatus"] == status and row["executionError"] == "stopped"
        assert prow["executionStatus"] == status


class _SlowS3:
    """An S3 client whose head/delete take `delay` seconds each, counting the most in flight at once."""

    def __init__(self, delay, metadata=None):
        import threading
        self.delay, self.metadata = delay, metadata or {}
        self.lock, self.now, self.peak, self.deleted = threading.Lock(), 0, 0, []

    def _enter(self):
        import time
        with self.lock:
            self.now += 1
            self.peak = max(self.peak, self.now)
        time.sleep(self.delay)
        with self.lock:
            self.now -= 1

    def head_object(self, Bucket, Key):
        self._enter()
        return {"Metadata": self.metadata}

    def delete_object(self, Bucket, Key, VersionId=None):
        self._enter()
        with self.lock:
            self.deleted.append((Key, VersionId))
        return {}


def _descriptors(n):
    return [{"fileType": "file", "relativeFilePath": f"f{i}.glb", "s3Bucket": "run",
             "s3Key": f"files/f{i}.glb", "s3VersionId": f"v{i}"} for i in range(n)]


@pytest.mark.unit
class TestTakeoverChecksAndDeletesRunConcurrently:
    """[in-dev D4] With 604 outputs the checks and deletes ran one at a time (16 s + 7 s) inside a 30 s
    function. They now run in the stamp step's bounded S3 pool."""

    def test_the_destination_checks_run_in_parallel(self):
        import time
        slow = _SlowS3(0.05, {po.VAMS_CHANGE_WORKFLOW_EXECUTION_ID_METADATA_KEY: EXEC})
        claimed = _descriptors(64)
        ledger = {eo.ingest_ledger_sort_key(d): {"ingestState": "moving", "ingestHolder": "attempt-0",
                                                 "s3VersionId": d["s3VersionId"]} for d in claimed}
        start = time.monotonic()
        with patch.object(po, "s3c", slow):
            landed, to_ingest = po._landed_takeovers(
                claimed, ledger, "attempt-1", {"assetId": "a1", "assetLocation": {"Key": "assets/a1/"}},
                "asset-bucket", "assets/", EXEC)
        elapsed = time.monotonic() - start
        assert len(landed) == 64 and to_ingest == []
        assert slow.peak > 1
        assert elapsed < 64 * 0.05 / 2  # a serial run takes 3.2 s

    def test_the_staged_version_deletes_run_in_parallel(self):
        import time
        slow = _SlowS3(0.05)
        start = time.monotonic()
        with patch.object(po, "s3c", slow):
            po._remove_listed_staged_versions(_descriptors(64), "run")
        assert slow.peak > 1 and len(slow.deleted) == 64
        assert time.monotonic() - start < 64 * 0.05 / 2


@pytest.mark.unit
class TestIncrementalSettle:
    """[in-dev D4] Rows are marked `ingested` batch by batch, each after its own staged deletes, so an
    attempt killed mid-way leaves the batches it finished."""

    def test_a_kill_mid_way_keeps_the_finished_batches(self):
        n = getattr(po, "SETTLE_BATCH_SIZE", 100) * 2 + 10
        descriptors = _descriptors(n)
        events, settled = [], []

        class _Killed(BaseException):
            pass

        def remove(batch, _bucket):
            events.append(("remove", len(batch)))
            if len(events) >= 5:  # the third batch's delete is cut short
                raise _Killed()

        def write(_execution_id, batch, state, upload_id=""):
            events.append(("settle", len(batch)))
            settled.extend(d["s3Key"] for d in batch)

        with patch.object(po, "_remove_listed_staged_versions", side_effect=remove), \
                patch.object(po, "_write_ingest_ledger", side_effect=write), \
                patch.object(po, "_drop_ingest_ledger_rows"):
            with pytest.raises(_Killed):
                po._finish_landed_takeovers(
                    [{**d, "_priorStagedVersionId": ""} for d in descriptors], EXEC, "attempt-1", "run")
        size = po.SETTLE_BATCH_SIZE
        assert events[:4] == [("remove", size), ("settle", size), ("remove", size), ("settle", size)]
        assert settled == [d["s3Key"] for d in descriptors[:2 * size]]


@pytest.mark.unit
class TestTheLogRefreshNearTheDeadline:
    """[in-dev D4] After the record is written, the optional log refresh is skipped when the attempt
    would be killed during it."""

    def _refresh(self, remaining_ms):
        context = MagicMock(**{"get_remaining_time_in_millis.return_value": remaining_ms})
        with patch.object(po, "workflow_execution_log_group_arn",
                          "arn:aws:logs:us-east-1:1:log-group:/vams/workflows:*"), \
                patch.object(po.elc, "capture_settled_execution_log", return_value=("", "")) as capture:
            po._refresh_execution_log(EXEC, WF_DB, WF_ID, "", "", context=context)
        return capture

    def test_skipped_with_little_time_left(self):
        assert not self._refresh(20_000).called

    def test_run_with_time_to_spare(self):
        assert self._refresh(600_000).called
