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
            with patch.object(po, "_remove_listed_staged_versions",
                              side_effect=lambda *a, **k: order.append("remove")), \
                    patch.object(po, "_settle_ingest_ledger",
                                 side_effect=lambda *a, **k: order.append("settle")):
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
    po._write_execution_outputs(
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
